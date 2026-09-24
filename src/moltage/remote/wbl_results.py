"""Bounded publication and rollback of one managed WBL result directory."""

from pathlib import PurePosixPath
from hashlib import sha256
import re
import shlex

from moltage.remote.executor import RemoteExecutor, RemotePathNotFoundError
from moltage.remote.step_inputs import remote_file_sha256, upload_new_files_atomically


class WblResultPublicationError(RuntimeError):
    """The result directory cannot be changed with verified ownership."""


class WblResultPublication:
    """Keep the prior result recoverable until its manifest replacement commits."""

    def __init__(
        self,
        executor: RemoteExecutor,
        project_root: str,
        token: str,
        previous_hashes: tuple[tuple[str, str], ...] | None = None,
    ) -> None:
        root = PurePosixPath(project_root)
        if (
            not root.is_absolute() or str(root) != project_root
            or ".." in root.parts or str(root) == "/"
            or not isinstance(token, str)
            or re.fullmatch(r"[A-Za-z0-9._-]+", token) is None
        ):
            raise WblResultPublicationError("WBL publication paths are invalid")
        self.executor = executor
        self.root = project_root
        self.token = token
        self.final = str(root / "wbl")
        self.staging = str(root / f".wbl.tmp-{token}")
        self.backup = str(root / f".wbl.previous-{token}")
        self.previous_manifest = str(root / f"wbl-previous-{token}.project.json")
        self.previous_manifest_hash: str | None = None
        self.previous_hashes = previous_hashes
        self.new_hashes: tuple[tuple[str, str], ...] | None = None
        self.staging_created = False

    def _exists(self, path: str) -> bool:
        try:
            self.executor.stat(path)
        except RemotePathNotFoundError:
            return False
        return True

    def _absent(self, path: str) -> None:
        if self._exists(path):
            raise WblResultPublicationError(f"WBL path already exists; no overwrite: {path}")

    def verify(self, directory: str, hashes: tuple[tuple[str, str], ...]) -> None:
        """Check an exact flat, regular-file set using server-side hashes."""
        expected = dict(hashes)
        if not expected or any(
            not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9_.-]+", name) is None
            or name in {".", ".."} for name in expected
        ):
            raise WblResultPublicationError("WBL artifact filenames are invalid")
        entries = self.executor.list_directory(directory)
        if any(entry.is_directory for entry in entries) or {e.name for e in entries} != set(expected):
            raise WblResultPublicationError(f"WBL directory differs from recorded artifacts: {directory}")
        paths = [str(PurePosixPath(directory) / name) for name in expected]
        checks = [f"test -d {shlex.quote(directory)}", f"test ! -L {shlex.quote(directory)}"]
        checks.extend(
            f"test -f {shlex.quote(path)} && test ! -L {shlex.quote(path)}"
            for path in paths
        )
        if self.executor.execute(" && ".join(checks)).exit_status != 0:
            raise WblResultPublicationError("WBL artifacts must be regular files, not symlinks")
        for name, digest in expected.items():
            if remote_file_sha256(self.executor, str(PurePosixPath(directory) / name)) != digest:
                raise WblResultPublicationError(f"WBL artifact checksum changed: {name}")

    def preflight(self) -> None:
        self._absent(self.staging)
        self._absent(self.backup)
        self._absent(self.previous_manifest)
        if self.previous_hashes is None:
            self._absent(self.final)
        else:
            self.verify(self.final, self.previous_hashes)

    def preserve_previous_manifest(self, data: bytes) -> None:
        """Retain the accepted settings/hashes before replacing them with RUNNING.

        This is recovery evidence, not an automatic retry or recovery journal.
        It is retained on an uncertain outcome and removed only after a verified
        successful publication or rollback.
        """
        if self.previous_hashes is None:
            return
        name = PurePosixPath(self.previous_manifest).name
        try:
            upload_new_files_atomically(
                self.executor, self.root, {name: data},
                temporary_id_factory=lambda: self.token, verify_on_server=True,
            )
        except Exception as error:
            raise WblResultPublicationError(
                "Unable to save previous WBL recovery metadata; the run was not started. "
                f"Inspect possible partial files at {self.previous_manifest} and "
                f"{self.previous_manifest}.tmp-{self.token}: {error}"
            ) from None
        self.previous_manifest_hash = sha256(data).hexdigest()

    def discard_previous_manifest(self) -> None:
        if self.previous_manifest_hash is None:
            return
        path = shlex.quote(self.previous_manifest)
        if (
            self.executor.execute(f"test -f {path} && test ! -L {path}").exit_status != 0
            or remote_file_sha256(self.executor, self.previous_manifest) != self.previous_manifest_hash
        ):
            raise WblResultPublicationError("Previous WBL recovery metadata changed; retained")
        if self.executor.execute(f"rm -f -- {path}").exit_status != 0:
            raise WblResultPublicationError("Previous WBL recovery metadata cleanup failed")
        self.previous_manifest_hash = None

    def discard_staging(self) -> None:
        """Remove only this run's empty or checksum-verified staging directory."""
        if not self.staging_created or not self._exists(self.staging):
            return
        entries = self.executor.list_directory(self.staging)
        if entries:
            expected = dict(self.new_hashes or ())
            names = {entry.name for entry in entries}
            if not names <= set(expected):
                raise WblResultPublicationError("Incomplete staging files retained for inspection")
            hashes = tuple((name, expected[name]) for name in sorted(names))
            self.verify(self.staging, hashes)
            paths = [str(PurePosixPath(self.staging) / name) for name, _ in hashes]
            command = "rm -f -- " + " ".join(shlex.quote(path) for path in paths)
            command += " && rmdir -- " + shlex.quote(self.staging)
        else:
            path = shlex.quote(self.staging)
            command = f"test -d {path} && test ! -L {path} && rmdir -- {path}"
        if self.executor.execute(command).exit_status != 0:
            raise WblResultPublicationError("WBL staging cleanup incomplete")

    def publish(self, hashes: tuple[tuple[str, str], ...]) -> None:
        self.new_hashes = hashes
        self.verify(self.staging, hashes)
        if self.previous_hashes is not None:
            self.verify(self.final, self.previous_hashes)
            self._absent(self.backup)
            self.executor.rename(self.final, self.backup)
        else:
            self._absent(self.final)
        self.executor.rename(self.staging, self.final)

    def rollback(self) -> None:
        """Only call after proving the manifest is still this run's RUNNING record.

        Inspect actual directory state, since a rename may have succeeded before
        its acknowledgement was lost. Never infer ownership from a name alone.
        """
        if self.previous_hashes is not None and not self._exists(self.backup):
            self.verify(self.final, self.previous_hashes)
            return
        if self._exists(self.final):
            if self.new_hashes is None:
                raise WblResultPublicationError("WBL publication outcome is unknown")
            self.verify(self.final, self.new_hashes)
            self._absent(self.staging)
            self.executor.rename(self.final, self.staging)
        if self.previous_hashes is not None:
            self.verify(self.backup, self.previous_hashes)
            self.executor.rename(self.backup, self.final)
            self.verify(self.final, self.previous_hashes)

    def discard_backup(self) -> None:
        """Delete only the verified old result set after the new manifest commits."""
        if self.previous_hashes is None:
            return
        self.verify(self.backup, self.previous_hashes)
        paths = [str(PurePosixPath(self.backup) / name) for name, _ in self.previous_hashes]
        command = "rm -f -- " + " ".join(shlex.quote(path) for path in paths)
        command += " && rmdir -- " + shlex.quote(self.backup)
        if self.executor.execute(command).exit_status != 0:
            raise WblResultPublicationError(f"Previous WBL result cleanup incomplete: {self.backup}")
        self.discard_previous_manifest()
