"""Read-only recognition and validation of an external ORCA optimization.

Pairing uses exact file stems because ORCA derives ``.gbw`` and ``.xyz`` from
its input name and the standard invocation redirects to the matching ``.out``.
No ORCA output metadata grammar is interpreted here: the repository reviews
only the markers owned by :mod:`moltage.orca.evidence`, so the input/geometry
cross-check below is the authoritative proof that the artifacts belong to one
calculation.

An input may instead read its starting coordinates through one
``*xyzfile <charge> <multiplicity> <path>`` header.  Those coordinates are
written inline, in the same ``* xyz`` grammar Moltage renders, into the
managed input, so every later reader sees an ordinary explicit block.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from moltage.domain.structure import MolecularStructure
from moltage.orca.evidence import OrcaEvidenceError, parse_orca_final_xyz, parse_orca_optimization_output
from moltage.orca.input_writer import (
    OrcaInputScientificIdentity,
    parse_rendered_orca_scientific_identity,
    parse_rendered_orca_structure,
    render_orca_xyz_block,
)
from moltage.structure.xyz import XYZParseError, parse_xyz


INPUT_EXTENSION = ".inp"
OUTPUT_EXTENSION = ".out"
GEOMETRY_EXTENSION = ".xyz"
WAVEFUNCTION_EXTENSION = ".gbw"
REQUIRED_EXTENSIONS = (
    INPUT_EXTENSION,
    OUTPUT_EXTENSION,
    GEOMETRY_EXTENSION,
    WAVEFUNCTION_EXTENSION,
)

# Written by ``orca_2mkl -molden`` and never read as WBL input; the WBL stage
# derives basis, MO coefficients and AO overlap from the ``.gbw`` instead.
MOLDEN_SUFFIX = ".molden.input"


class OrcaImportEvidenceError(ValueError):
    """Raised when an external ORCA directory cannot prove a usable result."""


@dataclass(frozen=True, slots=True)
class OrcaImportCandidate:
    """One complete stem-paired ORCA optimization result group."""

    stem: str

    @property
    def input_filename(self) -> str:
        return self.stem + INPUT_EXTENSION

    @property
    def output_filename(self) -> str:
        return self.stem + OUTPUT_EXTENSION

    @property
    def geometry_filename(self) -> str:
        return self.stem + GEOMETRY_EXTENSION

    @property
    def wavefunction_filename(self) -> str:
        return self.stem + WAVEFUNCTION_EXTENSION

    @property
    def filenames(self) -> tuple[str, str, str, str]:
        return (
            self.input_filename,
            self.output_filename,
            self.geometry_filename,
            self.wavefunction_filename,
        )


@dataclass(frozen=True, slots=True)
class OrcaImportPartialCandidate:
    """One stem that looks like an ORCA run but lacks required artifacts."""

    stem: str
    missing_extensions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OrcaImportCandidateSurvey:
    complete: tuple[OrcaImportCandidate, ...]
    partial: tuple[OrcaImportPartialCandidate, ...]
    molden_filenames: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OrcaImportOptimizationEvidence:
    """Verified external optimization facts drawn from existing ORCA parsers."""

    candidate: OrcaImportCandidate
    submitted_structure: MolecularStructure
    optimized_structure: MolecularStructure
    identity: OrcaInputScientificIdentity
    # The managed ``orca_opt.inp`` when the source reads ``*xyzfile``
    # coordinates; ``None`` means the source input is copied unchanged.
    inlined_input: bytes | None = None

    @property
    def elements(self) -> tuple[str, ...]:
        return tuple(atom.element for atom in self.submitted_structure)


@dataclass(frozen=True, slots=True)
class _XyzfileReference:
    line_index: int
    charge: int
    multiplicity: int
    path: str


def survey_orca_import_candidates(
    filenames: Iterable[str],
) -> OrcaImportCandidateSurvey:
    """Group non-empty regular file names into complete and partial results."""

    names = {name for name in filenames if isinstance(name, str) and name}
    stems: dict[str, set[str]] = {}
    for name in names:
        for extension in REQUIRED_EXTENSIONS:
            if name.endswith(extension) and len(name) > len(extension):
                stems.setdefault(name[: -len(extension)], set()).add(extension)
                break
    complete = []
    partial = []
    for stem in sorted(stems):
        missing = tuple(
            extension
            for extension in REQUIRED_EXTENSIONS
            if extension not in stems[stem]
        )
        if missing:
            partial.append(OrcaImportPartialCandidate(stem, missing))
        else:
            complete.append(OrcaImportCandidate(stem))
    molden = tuple(sorted(name for name in names if name.endswith(MOLDEN_SUFFIX)))
    return OrcaImportCandidateSurvey(tuple(complete), tuple(partial), molden)


def describe_missing_candidates(survey: OrcaImportCandidateSurvey) -> str:
    """Explain, in user-facing wording, why no complete result was found."""

    if survey.complete:
        raise OrcaImportEvidenceError(
            "complete ORCA results were found; no missing-result description applies"
        )
    if not survey.partial:
        return (
            "This directory contains no ORCA optimization result. Moltage needs "
            "one calculation providing .inp, .out, .xyz and .gbw files that share "
            "the same name."
        )
    listed = ", ".join(
        f"{item.stem} (missing {', '.join(item.missing_extensions)})"
        for item in survey.partial[:5]
    )
    return (
        "No ORCA calculation in this directory provides all required files. "
        f"Closest matches: {listed}."
    )


def resolve_orca_xyzfile_path(
    candidate: OrcaImportCandidate,
    input_bytes: bytes,
    source_directory: str,
) -> str | None:
    """Return the absolute coordinate file an ``*xyzfile`` input reads, if any.

    A relative name is resolved against the source directory, the working
    directory of the standard ``orca <stem>.inp > <stem>.out`` invocation that
    stem pairing already assumes.
    """

    reference = _xyzfile_reference(candidate, input_bytes)
    if reference is None:
        return None
    path = PurePosixPath(source_directory) / reference.path
    if ".." in path.parts:
        raise OrcaImportEvidenceError(
            f"{candidate.input_filename} names its coordinate file "
            f"{reference.path} through '..'; Moltage reads only a normalized path."
        )
    resolved = str(path)
    if resolved == str(PurePosixPath(source_directory) / candidate.geometry_filename):
        raise OrcaImportEvidenceError(
            f"{candidate.input_filename} reads its starting coordinates from "
            f"{candidate.geometry_filename}, which now holds the final optimized "
            "geometry; the submitted structure cannot be recovered."
        )
    return resolved


def validate_orca_import_optimization(
    candidate: OrcaImportCandidate,
    *,
    input_bytes: bytes,
    output_bytes: bytes,
    geometry_bytes: bytes,
    coordinate_bytes: bytes | None = None,
) -> OrcaImportOptimizationEvidence:
    """Prove external optimization success with the reviewed Step-1 rules.

    ``coordinate_bytes`` is the file named by an ``*xyzfile`` header and is
    required exactly when the input has one.
    """

    if not isinstance(candidate, OrcaImportCandidate):
        raise TypeError("import validation requires an OrcaImportCandidate")
    reference = _xyzfile_reference(candidate, input_bytes)
    if reference is None:
        if coordinate_bytes is not None:
            raise ValueError("coordinate bytes apply only to an *xyzfile input")
        managed_input = input_bytes
        unreadable = (
            f"{candidate.input_filename} does not contain a readable explicit "
            "ORCA coordinate block"
        )
    else:
        if coordinate_bytes is None:
            raise OrcaImportEvidenceError(
                f"{candidate.input_filename} reads its starting coordinates from "
                f"{reference.path}; that file is required."
            )
        managed_input = _inline_coordinates(
            candidate, input_bytes, reference, coordinate_bytes
        )
        unreadable = f"{reference.path} does not provide a readable coordinate block"
    try:
        submitted = parse_rendered_orca_structure(managed_input)
        identity = parse_rendered_orca_scientific_identity(managed_input)
    except (TypeError, ValueError) as error:
        raise OrcaImportEvidenceError(f"{unreadable}: {error}") from None
    try:
        parsed = parse_orca_optimization_output(output_bytes)
    except OrcaEvidenceError as error:
        raise OrcaImportEvidenceError(
            f"{candidate.output_filename} could not be read: {error}"
        ) from None
    if parsed.explicit_nonconvergence:
        raise OrcaImportEvidenceError(
            f"{candidate.output_filename} reports that the geometry optimization "
            "did not converge."
        )
    if not parsed.normal_termination:
        raise OrcaImportEvidenceError(
            f"{candidate.output_filename} does not confirm ORCA normal termination."
        )
    if not parsed.optimization_converged:
        raise OrcaImportEvidenceError(
            f"{candidate.output_filename} does not confirm geometry-optimization "
            "convergence."
        )
    try:
        optimized = parse_orca_final_xyz(geometry_bytes, submitted)
    except OrcaEvidenceError as error:
        raise OrcaImportEvidenceError(
            f"{candidate.geometry_filename} is not the final geometry of "
            f"{candidate.input_filename}: {error}"
        ) from None
    return OrcaImportOptimizationEvidence(
        candidate,
        submitted,
        optimized,
        identity,
        None if reference is None else managed_input,
    )


def _xyzfile_reference(candidate, input_bytes) -> _XyzfileReference | None:
    name = candidate.input_filename
    lines = _input_text(candidate, input_bytes).splitlines()
    headers = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        fields = stripped[1:].split() if stripped.startswith("*") else []
        if fields and fields[0].lower() == "xyzfile":
            headers.append((index, fields[1:]))
    if not headers:
        return None
    if len(headers) > 1:
        raise OrcaImportEvidenceError(
            f"{name} contains more than one *xyzfile coordinate header."
        )
    if any(line.strip().lower().startswith("* xyz ") for line in lines):
        raise OrcaImportEvidenceError(
            f"{name} contains both an inline * xyz block and an *xyzfile header."
        )
    index, fields = headers[0]
    if len(fields) != 3:
        raise OrcaImportEvidenceError(
            f"{name} has an *xyzfile header that does not name exactly a charge, "
            "a multiplicity and one coordinate file."
        )
    try:
        charge, multiplicity = int(fields[0]), int(fields[1])
    except ValueError:
        raise OrcaImportEvidenceError(
            f"{name} has an *xyzfile header without integer charge and multiplicity."
        ) from None
    if multiplicity < 1:
        raise OrcaImportEvidenceError(
            f"{name} has an *xyzfile header with a non-positive multiplicity."
        )
    return _XyzfileReference(index, charge, multiplicity, fields[2])


def _inline_coordinates(candidate, input_bytes, reference, coordinate_bytes) -> bytes:
    """Replace only the ``*xyzfile`` line; every other input line is kept."""

    try:
        structure = parse_xyz(coordinate_bytes.decode("utf-8"))
    except (UnicodeDecodeError, XYZParseError) as error:
        raise OrcaImportEvidenceError(
            f"{reference.path} is not a readable XYZ coordinate file: {error}"
        ) from None
    lines = _input_text(candidate, input_bytes).splitlines(keepends=True)
    header = lines[reference.line_index]
    ending = header[len(header.splitlines()[0]):]
    block = render_orca_xyz_block(structure, reference.charge, reference.multiplicity)
    lines[reference.line_index] = (ending or "\n").join(block) + ending
    return "".join(lines).encode("utf-8")


def _input_text(candidate, input_bytes) -> str:
    if not isinstance(input_bytes, bytes):
        raise TypeError("ORCA input must be bytes")
    try:
        return input_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise OrcaImportEvidenceError(
            f"{candidate.input_filename} is not valid UTF-8 text."
        ) from None
