"""Locate immutable application resources in source and frozen runtimes."""

from pathlib import Path
import sys


def application_resources_directory() -> Path:
    """Return the root containing the application's bundled resources."""

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root is not None:
        return Path(bundle_root).resolve() / "resources"
    return Path(__file__).resolve().parents[3] / "resources"


def application_resource_path(*parts: str) -> Path:
    """Return one resource path relative to the application resource root."""

    return application_resources_directory().joinpath(*parts)


def application_legal_document_path(filename: str) -> Path:
    """Return a bundled legal document in source and frozen runtimes."""

    if not filename or Path(filename).name != filename:
        raise ValueError("legal document filename must be one plain filename")
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root is not None:
        return (
            Path(bundle_root).resolve()
            / "resources"
            / "legal"
            / filename
        )
    return Path(__file__).resolve().parents[3] / filename
