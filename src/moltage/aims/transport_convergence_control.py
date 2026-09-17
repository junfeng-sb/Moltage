"""Render the reviewed FHI-aims Step-3 transport-convergence control.in."""

from collections.abc import Iterable, Mapping
import re

from moltage.aims.optimization_settings import (
    SpinInitializationMode,
    XCFunctional,
)
from moltage.aims.species_library import SpeciesDefaultBlock
from moltage.aims.transport_convergence_settings import (
    TransportConvergenceSettings,
)
from moltage.aims.orbital_cube import render_orbital_cube_directives
from moltage.domain.structure import MolecularStructure


class TransportConvergenceControlError(ValueError):
    """Raised when canonical Step-3 control.in rendering cannot proceed."""


_XC_KEYWORDS: Mapping[XCFunctional, str] = {
    XCFunctional.PBE: "pbe",
    XCFunctional.PBE0: "pbe0",
    XCFunctional.BLYP: "blyp",
    XCFunctional.B3LYP: "b3lyp",
    XCFunctional.REVPBE: "revpbe",
    XCFunctional.AM05: "am05",
}

_SPECIES_DECLARATION = re.compile(
    r"[ \t]*species[ \t]+(?P<name>[^\s#]+)[ \t]*(?:#.*)?"
)


def render_transport_convergence_control_in(
    settings: TransportConvergenceSettings,
    species_blocks: Iterable[SpeciesDefaultBlock],
    structure: MolecularStructure | None = None,
) -> str:
    """Render Step 3 globals in canonical order, then required species."""

    if not isinstance(settings, TransportConvergenceSettings):
        raise TypeError("settings must be TransportConvergenceSettings")
    try:
        xc_keyword = _XC_KEYWORDS[settings.xc]
    except KeyError as error:
        raise TransportConvergenceControlError(
            "unsupported Step-3 XC functional reached the renderer"
        ) from error

    blocks = tuple(species_blocks)
    if not blocks:
        raise TransportConvergenceControlError(
            "Step-3 control.in requires at least one species block"
        )
    block_texts: list[str] = []
    species_names: set[str] = set()
    for block in blocks:
        if not isinstance(block, SpeciesDefaultBlock):
            raise TypeError(
                "species blocks must contain SpeciesDefaultBlock records"
            )
        if block.species_name in species_names:
            raise TransportConvergenceControlError(
                f"duplicate Step-3 species block: {block.species_name}"
            )
        declarations = tuple(
            match.group("name")
            for line in block.text.splitlines()
            if (match := _SPECIES_DECLARATION.fullmatch(line)) is not None
        )
        if declarations != (block.species_name,):
            raise TransportConvergenceControlError(
                "species block must contain exactly one matching declaration: "
                f"{block.species_name}"
            )
        species_names.add(block.species_name)
        block_texts.append("\n".join(block.text.splitlines()))

    physical_lines = [
        f"xc {xc_keyword}",
        "spin collinear" if settings.spin.enabled else "spin none",
    ]
    if settings.spin.enabled:
        if (
            settings.spin.initialization_mode
            is not SpinInitializationMode.UNIFORM_DEFAULT
        ):
            raise TransportConvergenceControlError(
                "unsupported Step-3 spin initialization reached the renderer"
            )
        physical_lines.append(
            "default_initial_moment "
            + _format_control_real(settings.spin.uniform_initial_moment)
        )
    physical_lines.extend(
        (
            "relativistic atomic_zora scalar",
            f"charge {_format_charge(settings.total_charge)}",
        )
    )

    scf_lines = [
        "occupation_type gaussian "
        + _format_control_real(settings.occupation_width),
        "mixer pulay",
        f"  n_max_pulay {settings.n_max_pulay}",
        "  charge_mix_param "
        + _format_control_real(settings.charge_mix_param),
        "sc_accuracy_rho "
        + _format_control_real(settings.sc_accuracy_rho),
        "sc_accuracy_eev "
        + _format_control_real(settings.sc_accuracy_eev),
        "sc_accuracy_etot "
        + _format_control_real(settings.sc_accuracy_etot),
        f"sc_iter_limit {settings.sc_iter_limit}",
    ]

    fixed_lines = [
        "output aitranss",
        "KS_method serial",
        "restart aims.restart",
    ]
    sections = [
        "# Step 3 physical model\n" + "\n".join(physical_lines),
        "# Step 3 SCF convergence\n" + "\n".join(scf_lines),
        "# Fixed AITRANSS preparation directives\n" + "\n".join(fixed_lines),
    ]
    orbital_lines = render_orbital_cube_directives(
        settings.orbital_cubes,
        structure,
    )
    if orbital_lines:
        sections.append("# Orbital Cube output\n" + "\n".join(orbital_lines))
    sections.append(
        "# Species\n"
        f"# Moltage species accuracy: {settings.species_accuracy.value}\n\n"
        + "\n\n".join(block_texts)
    )
    return "\n\n".join(sections) + "\n"


def _format_charge(value: float) -> str:
    numeric = float(value)
    return f"{int(numeric)}." if numeric.is_integer() else _format_control_real(
        numeric
    )


def _format_control_real(value: float | None) -> str:
    if value is None:
        raise TransportConvergenceControlError(
            "required Step-3 numerical value is missing"
        )
    numeric = float(value)
    if numeric != 0.0 and abs(numeric) < 1.0e-2:
        mantissa, exponent = f"{numeric:.15e}".split("e")
        mantissa = mantissa.rstrip("0").rstrip(".")
        return f"{mantissa}E{int(exponent)}"
    text = f"{numeric:.15g}"
    if "e" not in text and "E" not in text:
        return text
    mantissa, exponent = re.split("[eE]", text)
    return f"{mantissa}E{int(exponent)}"
