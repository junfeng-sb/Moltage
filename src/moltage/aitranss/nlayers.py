"""Evidence-bound initial AITRANSS `$nlayers` values for Au pyramids."""

from dataclasses import dataclass
from enum import StrEnum

from moltage.domain.au_pyramid import validate_pyramid_layers


class NlayersValueSource(StrEnum):
    """Why a new Step-4 editor initially contains one `$nlayers` value."""

    AIMS_RECOMMENDED = "AIMS_RECOMMENDED"
    USER_SPECIFIED = "USER_SPECIFIED"


@dataclass(frozen=True, slots=True)
class NlayersInitialValue:
    value: int
    source: NlayersValueSource
    evidence: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, int) or self.value <= 0:
            raise ValueError("initial nlayers value must be a positive integer")
        if not isinstance(self.source, NlayersValueSource):
            raise TypeError("initial nlayers source is invalid")
        if self.source is NlayersValueSource.AIMS_RECOMMENDED:
            if not isinstance(self.evidence, str) or not self.evidence.strip():
                raise ValueError("AIMS-recommended nlayers requires reviewed evidence")
        elif self.evidence is not None:
            raise ValueError("user-specified nlayers must not claim AIMS evidence")


_REVIEWED_INITIAL_VALUES = {
    4: NlayersInitialValue(
        2,
        NlayersValueSource.AIMS_RECOMMENDED,
        "AITRANSS 051414 fcc(111) Au20 core plus two variant adatoms; "
        "all reviewed natoms22 headers specify N_a/$nlayers=2.",
    ),
    5: NlayersInitialValue(
        3,
        NlayersValueSource.AIMS_RECOMMENDED,
        "AITRANSS 051414 fcc(111) Au35 core plus two variant adatoms; "
        "all reviewed natoms37 headers specify N_a/$nlayers=3.",
    ),
    6: NlayersInitialValue(4, NlayersValueSource.USER_SPECIFIED),
}


def nlayers_initial_value_for_pyramid(
    pyramid_layers: int,
) -> NlayersInitialValue | None:
    """Return only reviewed/user-approved table entries; never derive a value."""

    layers = validate_pyramid_layers(pyramid_layers)
    return _REVIEWED_INITIAL_VALUES.get(layers)


def missing_nlayers_guidance(pyramid_layers: int) -> str:
    """Explain how to complete an uncovered value without guessing."""

    layers = validate_pyramid_layers(pyramid_layers)
    return (
        f"No reviewed `$nlayers` default is available for a {layers}-layer Au "
        "pyramid. Enter a positive integer after reviewing a similar Au cluster "
        "header in your licensed FHI-aims/AITRANSS electrodes.library installation."
    )
