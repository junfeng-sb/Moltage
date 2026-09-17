"""Render the curated Phase 2A FHI-aims optimization control.in."""

from collections.abc import Iterable, Mapping
import re

from moltage.aims.optimization_settings import (
    AimsOptimizationSettings,
    Relativity,
    SpinInitializationMode,
    VdwMethod,
    XCFunctional,
)
from moltage.aims.orbital_cube import render_orbital_cube_directives
from moltage.aims.species_library import SpeciesDefaultBlock
from moltage.domain.structure import MolecularStructure


class AimsControlGenerationError(ValueError):
    """Raised when deterministic control.in generation cannot proceed."""


_XC_KEYWORDS: Mapping[XCFunctional, str] = {
    XCFunctional.PBE: "pbe",
    XCFunctional.PBE0: "pbe0",
    XCFunctional.BLYP: "blyp",
    XCFunctional.B3LYP: "b3lyp",
    XCFunctional.REVPBE: "revpbe",
    XCFunctional.AM05: "am05",
}

_VDW_KEYWORDS: Mapping[VdwMethod, str | None] = {
    VdwMethod.NONE: None,
    VdwMethod.TS_HIRSHFELD: "vdw_correction_hirshfeld",
    VdwMethod.TS_LIBMBD: "vdw_ts",
}

_RELATIVITY_KEYWORDS: Mapping[Relativity, str] = {
    Relativity.ATOMIC_ZORA_SCALAR: "atomic_zora scalar",
    Relativity.NONE: "none",
}

_HYBRID_FUNCTIONALS = frozenset({XCFunctional.PBE0, XCFunctional.B3LYP})
_SPECIES_DECLARATION = re.compile(
    r"[ \t]*species[ \t]+(?P<name>[^\s#]+)[ \t]*(?:#.*)?"
)


def render_control_in(
    settings: AimsOptimizationSettings,
    species_blocks: Iterable[SpeciesDefaultBlock],
    structure: MolecularStructure | None = None,
) -> str:
    """Render a validated deterministic optimization control.in."""

    if not isinstance(settings, AimsOptimizationSettings):
        raise TypeError("settings must be AimsOptimizationSettings")

    xc_keyword = _mapped(_XC_KEYWORDS, settings.xc, "XC functional")
    vdw_keyword = _mapped(_VDW_KEYWORDS, settings.vdw, "vdW method")
    relativity_keyword = _mapped(
        _RELATIVITY_KEYWORDS,
        settings.relativity,
        "relativity setting",
    )
    blocks = tuple(species_blocks)
    if not blocks:
        raise AimsControlGenerationError(
            "control.in requires at least one species block"
        )
    names: list[str] = []
    normalized_block_texts: list[str] = []
    for block in blocks:
        if not isinstance(block, SpeciesDefaultBlock):
            raise TypeError(
                "species blocks must contain SpeciesDefaultBlock records"
            )
        if block.species_name in names:
            raise AimsControlGenerationError(
                f"duplicate generated species block: {block.species_name}"
            )
        declaration_names = tuple(
            match.group("name")
            for line in block.text.splitlines()
            if (match := _SPECIES_DECLARATION.fullmatch(line)) is not None
        )
        if declaration_names != (block.species_name,):
            raise AimsControlGenerationError(
                "species block must contain exactly one matching declaration: "
                f"{block.species_name}"
            )
        names.append(block.species_name)
        normalized_block_texts.append(
            "\n".join(block.text.splitlines()) + "\n"
        )

    physical_lines = [
        f"xc {xc_keyword}",
        "spin collinear" if settings.spin.enabled else "spin none",
    ]
    if settings.spin.enabled:
        if (
            settings.spin.initialization_mode
            is SpinInitializationMode.UNIFORM_DEFAULT
        ):
            physical_lines.append(
                "default_initial_moment "
                + _format_general_real(settings.spin.uniform_initial_moment)
            )
        if settings.spin.fixed_spin_moment is not None:
            physical_lines.append(
                "fixed_spin_moment "
                + _format_compact_real(settings.spin.fixed_spin_moment)
            )
    physical_lines.extend(
        (
            f"charge {_format_charge(settings.total_charge)}",
            f"relativistic {relativity_keyword}",
        )
    )
    if settings.xc in _HYBRID_FUNCTIONALS:
        physical_lines.append("RI_method LVL_fast")

    relaxation_lines = [
        "relax_geometry bfgs "
        + _format_scientific_real(settings.force_threshold)
    ]
    if vdw_keyword is not None:
        relaxation_lines.append(vdw_keyword)

    sections = [
        "# Physical model\n" + "\n".join(physical_lines),
        "# Relaxation\n" + "\n".join(relaxation_lines),
    ]
    output_lines: list[str] = []
    if settings.output_dipole:
        output_lines.append("output dipole")
    output_lines.extend(
        render_orbital_cube_directives(settings.orbital_cubes, structure)
    )
    if output_lines:
        sections.append("# Output\n" + "\n".join(output_lines))
    sections.append(
        "# Species\n"
        f"# Moltage species accuracy: {settings.species_accuracy.value}\n\n"
        + "\n".join(
            text.rstrip("\n") for text in normalized_block_texts
        )
    )
    return "\n\n".join(sections) + "\n"


def _mapped(mapping: Mapping[object, object], key: object, label: str):
    try:
        return mapping[key]
    except (KeyError, TypeError) as error:
        raise AimsControlGenerationError(
            f"unsupported {label} reached the control.in writer: {key!r}"
        ) from error


def _format_general_real(value: float | None) -> str:
    if value is None:
        raise AimsControlGenerationError("required numerical value is missing")
    return repr(float(value))


def _format_compact_real(value: float) -> str:
    numeric = float(value)
    return str(int(numeric)) if numeric.is_integer() else repr(numeric)


def _format_charge(value: float) -> str:
    numeric = float(value)
    return f"{int(numeric)}." if numeric.is_integer() else repr(numeric)


def _format_scientific_real(value: float) -> str:
    mantissa, exponent = f"{float(value):.15e}".split("e")
    mantissa = mantissa.rstrip("0")
    if mantissa.endswith("."):
        normalized_mantissa = mantissa
    else:
        normalized_mantissa = mantissa.rstrip(".")
    return f"{normalized_mantissa}e{int(exponent)}"
