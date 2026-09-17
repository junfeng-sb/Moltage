"""Reviewed ORCA options and version evidence used by structured controls."""

from dataclasses import dataclass
from enum import StrEnum
import re


class OrcaCatalogError(ValueError):
    """Raised when an ORCA version or catalog choice is unsupported."""


class OrcaVersionFamily(StrEnum):
    V5_0 = "5.0"
    V6_0 = "6.0"
    V6_1 = "6.1"


class OrcaMethod(StrEnum):
    BP86 = "BP86"
    BLYP = "BLYP"
    PBE = "PBE"
    TPSS = "TPSS"
    B3LYP = "B3LYP"
    PBE0 = "PBE0"
    M062X = "M062X"
    TPSSH = "TPSSH"
    B97_3C = "B97-3C"
    PBEH_3C = "PBEH-3C"
    R2SCAN_3C = "R2SCAN-3C"
    WB97M_D4REV = "WB97M-D4REV"


class OrcaBasis(StrEnum):
    DEF2_SVP = "DEF2-SVP"
    DEF2_TZVP = "DEF2-TZVP"
    DEF2_TZVPP = "DEF2-TZVPP"
    DEF2_QZVP = "DEF2-QZVP"


# The reviewed ORCA def2 table documents the four exposed orbital bases for
# H--Rn.  Moltage does not silently substitute another basis outside that
# range.
DEF2_MIN_ATOMIC_NUMBER = 1
DEF2_MAX_ATOMIC_NUMBER = 86
BASIS_MANUAL_URL = (
    "https://www.faccts.de/docs/orca/6.1/manual/contents/"
    "essentialelements/basisset.html"
)


class OrcaDispersion(StrEnum):
    NONE = "NONE"
    D3ZERO = "D3ZERO"
    D3BJ = "D3BJ"
    D4 = "D4"


class OrcaOptimizationConvergence(StrEnum):
    LOOSEOPT = "LOOSEOPT"
    OPT = "OPT"
    TIGHTOPT = "TIGHTOPT"
    VERYTIGHTOPT = "VERYTIGHTOPT"


class OrcaCoordinateSystem(StrEnum):
    REDUNDANT = "REDUNDANT"
    CARTESIAN = "CARTESIAN"


class OrcaScfConvergence(StrEnum):
    DEFAULT = "DEFAULT"
    STRONGSCF = "STRONGSCF"
    TIGHTSCF = "TIGHTSCF"
    VERYTIGHTSCF = "VERYTIGHTSCF"


class OrcaFrequencyMode(StrEnum):
    FREQ = "FREQ"
    NUMFREQ = "NUMFREQ"


@dataclass(frozen=True, slots=True)
class OrcaVersionEvidence:
    """Literal version output plus the supported family it proves, if any."""

    version_text: str
    version: str | None
    version_family: OrcaVersionFamily | None
    detection_source: str

    @property
    def supported(self) -> bool:
        return self.version_family is not None


@dataclass(frozen=True, slots=True)
class OrcaMethodCapability:
    method: OrcaMethod
    families: frozenset[OrcaVersionFamily]
    requires_basis: bool
    permits_dispersion: bool
    analytical_frequency_supported: bool
    manual_url: str


_ALL_FAMILIES = frozenset(OrcaVersionFamily)
_V6_FAMILIES = frozenset({OrcaVersionFamily.V6_0, OrcaVersionFamily.V6_1})
_DFT_MANUAL = "https://www.faccts.de/docs/orca/6.1/manual/contents/modelchemistries/DensityFunctionalTheory.html"
_COMPOSITE_MANUAL = "https://www.faccts.de/docs/orca/6.1/manual/contents/modelchemistries/composite_methods.html"


def _standard(method: OrcaMethod) -> OrcaMethodCapability:
    return OrcaMethodCapability(method, _ALL_FAMILIES, True, True, True, _DFT_MANUAL)


METHOD_CAPABILITIES: dict[OrcaMethod, OrcaMethodCapability] = {
    method: _standard(method)
    for method in (
        OrcaMethod.BP86,
        OrcaMethod.BLYP,
        OrcaMethod.PBE,
        OrcaMethod.TPSS,
        OrcaMethod.B3LYP,
        OrcaMethod.PBE0,
        OrcaMethod.M062X,
        OrcaMethod.TPSSH,
    )
}
METHOD_CAPABILITIES.update(
    {
        OrcaMethod.B97_3C: OrcaMethodCapability(
            OrcaMethod.B97_3C, _ALL_FAMILIES, False, False, True, _COMPOSITE_MANUAL
        ),
        OrcaMethod.PBEH_3C: OrcaMethodCapability(
            OrcaMethod.PBEH_3C, _ALL_FAMILIES, False, False, True, _COMPOSITE_MANUAL
        ),
        OrcaMethod.R2SCAN_3C: OrcaMethodCapability(
            OrcaMethod.R2SCAN_3C, _V6_FAMILIES, False, False, True, _COMPOSITE_MANUAL
        ),
        OrcaMethod.WB97M_D4REV: OrcaMethodCapability(
            OrcaMethod.WB97M_D4REV, _V6_FAMILIES, True, False, True, _DFT_MANUAL
        ),
    }
)

_VERSION_PATTERNS = (
    re.compile(r"(?im)^\s*Program\s+Version\s+(?P<version>[0-9]+\.[0-9]+(?:\.[0-9]+)?)\b"),
    re.compile(r"(?im)\bORCA(?:\s+VERSION)?\s+(?P<version>[0-9]+\.[0-9]+(?:\.[0-9]+)?)\b"),
)


def parse_orca_version_evidence(
    output: str | bytes,
    *,
    detection_source: str,
) -> OrcaVersionEvidence:
    """Parse literal ORCA output without inferring a version from paths/modules."""

    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    if not isinstance(output, str) or not isinstance(detection_source, str) or not detection_source.strip():
        raise TypeError("ORCA version output and detection source must be text")
    version = None
    for pattern in _VERSION_PATTERNS:
        match = pattern.search(output)
        if match is not None:
            version = match.group("version")
            break
    family = None
    if version is not None:
        major_minor = ".".join(version.split(".")[:2])
        try:
            family = OrcaVersionFamily(major_minor)
        except ValueError:
            family = None
    return OrcaVersionEvidence(
        version_text=output.strip()[:4000],
        version=version,
        version_family=family,
        detection_source=detection_source.strip(),
    )


def method_capability(method: OrcaMethod | str) -> OrcaMethodCapability:
    try:
        return METHOD_CAPABILITIES[OrcaMethod(method)]
    except (KeyError, ValueError):
        raise OrcaCatalogError(f"Unsupported ORCA method: {method!r}") from None


def methods_for_version(family: OrcaVersionFamily | None) -> tuple[OrcaMethod, ...]:
    """Return only options supported by evidence; None means version-common only."""

    if family is not None:
        family = OrcaVersionFamily(family)
    return tuple(
        method
        for method, capability in METHOD_CAPABILITIES.items()
        if capability.families == _ALL_FAMILIES
        or (family is not None and family in capability.families)
    )


def require_supported_method(
    method: OrcaMethod | str,
    family: OrcaVersionFamily | None,
) -> OrcaMethodCapability:
    capability = method_capability(method)
    if family is None:
        if capability.families != _ALL_FAMILIES:
            raise OrcaCatalogError(
                f"{capability.method.value} requires verified ORCA version evidence"
            )
    elif OrcaVersionFamily(family) not in capability.families:
        raise OrcaCatalogError(
            f"{capability.method.value} is not supported for ORCA {family.value}"
        )
    return capability
