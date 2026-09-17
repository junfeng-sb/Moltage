"""Strictly recover settings from input pairs emitted by this application."""

from dataclasses import dataclass
from pathlib import PurePosixPath
import re

from moltage.aims.input_bundle import (
    build_aims_optimization_inputs,
)
from moltage.aims.optimization_settings import (
    AimsOptimizationSettings,
    AtomAimsSettings,
    Relativity,
    SpeciesAccuracy,
    SpinInitializationMode,
    SpinSettings,
    VdwMethod,
    XCFunctional,
)
from moltage.aims.orbital_cube import (
    OrbitalCubeValidationError,
    parse_generated_orbital_cube_directives,
)
from moltage.aims.recovery import (
    AimsRecoveryError,
    parse_control_species_elements,
    parse_molecular_geometry,
)
from moltage.aims.species_library import (
    InMemorySpeciesLibrary,
    SpeciesDefaultBlock,
    species_default_block_from_text,
)
from moltage.aims.transport_convergence_bundle import (
    build_transport_convergence_aims_inputs,
)
from moltage.aims.transport_convergence_settings import (
    TransportConvergenceSettings,
)
from moltage.domain.structure import MolecularStructure


class AimsInputParsingError(ValueError):
    """Raised unless a pair exactly round-trips through the accepted writers."""


@dataclass(frozen=True, slots=True)
class ParsedOptimizationInputs:
    structure: MolecularStructure
    settings: AimsOptimizationSettings


@dataclass(frozen=True, slots=True)
class ParsedTransportConvergenceInputs:
    structure: MolecularStructure
    settings: TransportConvergenceSettings


def parse_generated_optimization_inputs(
    geometry_text: str | bytes,
    control_text: str | bytes,
) -> ParsedOptimizationInputs:
    """Recover one unmodified Phase-2A pair; ambiguity fails explicitly."""

    geometry = _decode(geometry_text, "geometry.in")
    control = _decode(control_text, "control.in")
    structure = _parse_structure(geometry, control)
    directives = _directives_before_species(control)
    try:
        orbital_cubes, directives = parse_generated_orbital_cube_directives(
            directives
        )
    except OrbitalCubeValidationError as error:
        raise AimsInputParsingError(str(error)) from None
    species_names, annotations = _geometry_metadata(geometry, structure)
    _validate_embedded_species_names(
        control,
        tuple(dict.fromkeys(species_names)),
    )

    xc = XCFunctional(_one_value(directives, "xc"))
    spin_directive = _one_value(directives, "spin")
    spin = _optimization_spin(directives, annotations, spin_directive)
    relativity_text = _one_value(directives, "relativistic")
    relativity = {
        "atomic_zora scalar": Relativity.ATOMIC_ZORA_SCALAR,
        "none": Relativity.NONE,
    }.get(relativity_text)
    if relativity is None:
        raise AimsInputParsingError(
            f"unsupported generated relativity setting: {relativity_text!r}"
        )
    vdw_lines = tuple(
        line
        for line in directives
        if line in {"vdw_correction_hirshfeld", "vdw_ts"}
    )
    if len(vdw_lines) > 1:
        raise AimsInputParsingError("control.in contains multiple vdW directives")
    vdw = {
        None: VdwMethod.NONE,
        "vdw_correction_hirshfeld": VdwMethod.TS_HIRSHFELD,
        "vdw_ts": VdwMethod.TS_LIBMBD,
    }[vdw_lines[0] if vdw_lines else None]
    relax = _one_value(directives, "relax_geometry")
    relax_fields = relax.split()
    if len(relax_fields) != 2 or relax_fields[0] != "bfgs":
        raise AimsInputParsingError(
            "generated optimization requires one relax_geometry bfgs value"
        )
    force_threshold = _float(relax_fields[1], "force threshold")
    charge = _float(_one_value(directives, "charge"), "total charge")
    output_dipole = directives.count("output dipole") == 1
    if sum(line.startswith("output ") for line in directives) != int(output_dipole):
        raise AimsInputParsingError(
            "control.in contains unsupported generated output directives"
        )

    accuracy_hint = _species_accuracy_hint(control)
    candidates: list[AimsOptimizationSettings] = []
    for global_accuracy in SpeciesAccuracy:
        if accuracy_hint is not None and global_accuracy is not accuracy_hint:
            continue
        try:
            atom_settings = _optimization_atom_settings(
                structure,
                species_names,
                annotations,
                global_accuracy,
            )
            candidate = AimsOptimizationSettings(
                xc=xc,
                vdw=vdw,
                relativity=relativity,
                species_accuracy=global_accuracy,
                force_threshold=force_threshold,
                output_dipole=output_dipole,
                orbital_cubes=orbital_cubes,
                total_charge=charge,
                spin=spin,
                atom_settings=atom_settings,
            )
            requirements = _optimization_species_requirements(
                structure,
                species_names,
                candidate,
            )
            species_library = _embedded_species_library(
                control,
                requirements,
                separator="\n",
                validate_legacy_accuracy=accuracy_hint is None,
            )
            rendered = build_aims_optimization_inputs(
                structure,
                candidate,
                species_library,
            )
        except (AimsInputParsingError, TypeError, ValueError):
            continue
        if rendered.geometry_text == geometry and _control_round_trip_matches(
            rendered.control_text,
            control,
        ):
            candidates.append(candidate)
    if len(candidates) != 1:
        raise AimsInputParsingError(
            "generated optimization settings are not uniquely recoverable "
            f"({len(candidates)} exact round-trip candidates)"
        )
    return ParsedOptimizationInputs(structure, candidates[0])


def parse_generated_transport_convergence_inputs(
    geometry_text: str | bytes,
    control_text: str | bytes,
) -> ParsedTransportConvergenceInputs:
    """Recover one unmodified Step-3 pair by exact writer round trip."""

    geometry = _decode(geometry_text, "geometry.in")
    control = _decode(control_text, "control.in")
    structure = _parse_structure(geometry, control)
    species_names, annotations = _geometry_metadata(geometry, structure)
    if annotations:
        raise AimsInputParsingError(
            "generated Step-3 geometry contains unsupported atom annotations"
        )
    _validate_embedded_species_names(
        control,
        tuple(dict.fromkeys(species_names)),
    )
    directives = _directives_before_species(control)
    try:
        orbital_cubes, directives = parse_generated_orbital_cube_directives(
            directives
        )
    except OrbitalCubeValidationError as error:
        raise AimsInputParsingError(str(error)) from None
    xc = XCFunctional(_one_value(directives, "xc"))
    spin_text = _one_value(directives, "spin")
    if spin_text == "none":
        spin = SpinSettings()
    elif spin_text == "collinear":
        spin = SpinSettings(
            enabled=True,
            initialization_mode=SpinInitializationMode.UNIFORM_DEFAULT,
            uniform_initial_moment=_float(
                _one_value(directives, "default_initial_moment"),
                "default initial moment",
            ),
        )
    else:
        raise AimsInputParsingError(
            f"unsupported generated Step-3 spin setting: {spin_text!r}"
        )
    occupation = _one_value(directives, "occupation_type").split()
    if len(occupation) != 2 or occupation[0] != "gaussian":
        raise AimsInputParsingError(
            "generated Step-3 occupation_type must be gaussian"
        )
    if _one_value(directives, "mixer") != "pulay":
        raise AimsInputParsingError("generated Step-3 mixer must be pulay")
    fixed = {
        "relativistic": "atomic_zora scalar",
        "output": "aitranss",
        "KS_method": "serial",
        "restart": "aims.restart",
    }
    for keyword, expected in fixed.items():
        if _one_value(directives, keyword) != expected:
            raise AimsInputParsingError(
                f"generated Step-3 {keyword} directive is unsupported"
            )

    accuracy_hint = _species_accuracy_hint(control)
    candidates: list[TransportConvergenceSettings] = []
    for accuracy in SpeciesAccuracy:
        if accuracy_hint is not None and accuracy is not accuracy_hint:
            continue
        try:
            candidate = TransportConvergenceSettings(
                xc=xc,
                spin=spin,
                total_charge=_float(
                    _one_value(directives, "charge"),
                    "total charge",
                ),
                species_accuracy=accuracy,
                occupation_width=_float(occupation[1], "occupation width"),
                n_max_pulay=_integer(
                    _one_value(directives, "n_max_pulay"),
                    "n_max_pulay",
                ),
                charge_mix_param=_float(
                    _one_value(directives, "charge_mix_param"),
                    "charge_mix_param",
                ),
                sc_accuracy_rho=_float(
                    _one_value(directives, "sc_accuracy_rho"),
                    "sc_accuracy_rho",
                ),
                sc_accuracy_eev=_float(
                    _one_value(directives, "sc_accuracy_eev"),
                    "sc_accuracy_eev",
                ),
                sc_accuracy_etot=_float(
                    _one_value(directives, "sc_accuracy_etot"),
                    "sc_accuracy_etot",
                ),
                sc_iter_limit=_integer(
                    _one_value(directives, "sc_iter_limit"),
                    "sc_iter_limit",
                ),
                orbital_cubes=orbital_cubes,
            )
            species_library = _embedded_species_library(
                control,
                tuple(
                    (element, element, accuracy)
                    for element in dict.fromkeys(
                        atom.element for atom in structure
                    )
                ),
                separator="\n\n",
                validate_legacy_accuracy=accuracy_hint is None,
            )
            rendered = build_transport_convergence_aims_inputs(
                structure,
                candidate,
                species_library,
            )
        except (TypeError, ValueError):
            continue
        if rendered.geometry_text == geometry and _control_round_trip_matches(
            rendered.control_text,
            control,
        ):
            candidates.append(candidate)
    if len(candidates) != 1:
        raise AimsInputParsingError(
            "generated Step-3 settings are not uniquely recoverable "
            f"({len(candidates)} exact round-trip candidates)"
        )
    return ParsedTransportConvergenceInputs(structure, candidates[0])


def _parse_structure(geometry: str, control: str) -> MolecularStructure:
    try:
        species = parse_control_species_elements(control)
        return parse_molecular_geometry(
            geometry,
            species,
            source_name="geometry.in",
        )
    except AimsRecoveryError as error:
        raise AimsInputParsingError(str(error)) from None


def _optimization_species_requirements(
    structure: MolecularStructure,
    species_names: tuple[str, ...],
    settings: AimsOptimizationSettings,
) -> tuple[tuple[str, str, SpeciesAccuracy], ...]:
    settings_by_index = {
        item.atom_index: item for item in settings.atom_settings
    }
    requirements: list[tuple[str, str, SpeciesAccuracy]] = []
    seen: set[str] = set()
    for atom, species_name in zip(structure, species_names, strict=True):
        if species_name in seen:
            continue
        atom_setting = settings_by_index.get(atom.index)
        accuracy = (
            settings.species_accuracy
            if atom_setting is None or atom_setting.species_accuracy is None
            else atom_setting.species_accuracy
        )
        requirements.append((species_name, atom.element, accuracy))
        seen.add(species_name)
    return tuple(requirements)


def _embedded_species_library(
    control: str,
    requirements: tuple[tuple[str, str, SpeciesAccuracy], ...],
    *,
    separator: str,
    validate_legacy_accuracy: bool,
) -> InMemorySpeciesLibrary:
    """Recover exact generated blocks without any external species library."""

    marker = "# Species"
    if control.count(marker) != 1:
        raise AimsInputParsingError(
            "generated control.in must contain exactly one # Species section"
        )
    suffix = control.split(marker, 1)[1]
    metadata = _SPECIES_ACCURACY_METADATA.match(suffix)
    if metadata is not None:
        suffix = suffix[metadata.end() :]
    if not suffix.startswith("\n\n") or not suffix.endswith("\n"):
        raise AimsInputParsingError(
            "generated control.in species section formatting is malformed"
        )
    body = suffix[2:-1]
    declarations = tuple(_EMBEDDED_SPECIES_DECLARATION.finditer(body))
    expected_names = tuple(name for name, _element, _accuracy in requirements)
    actual_names = tuple(match.group("name") for match in declarations)
    if actual_names != expected_names:
        raise AimsInputParsingError(
            "generated control.in species blocks do not match geometry order: "
            f"geometry={expected_names}, control={actual_names}"
        )
    if not declarations:
        raise AimsInputParsingError(
            "generated control.in contains no embedded species blocks"
        )

    boundaries: list[int] = []
    for previous, current in zip(declarations, declarations[1:], strict=False):
        boundary = body.rfind(separator, previous.end(), current.start())
        if boundary < 0:
            raise AimsInputParsingError(
                "generated control.in species block boundaries are ambiguous"
            )
        boundaries.append(boundary)
    starts = [0, *(boundary + len(separator) for boundary in boundaries)]
    ends = [*boundaries, len(body)]
    blocks: list[SpeciesDefaultBlock] = []
    legacy_accuracy_hints = _legacy_species_accuracy_hints(
        body,
        declarations,
    )
    block_ranges = zip(requirements, starts, ends, strict=True)
    for block_index, (
        (species_name, element, accuracy),
        start,
        end,
    ) in enumerate(block_ranges):
        block_text = body[start:end]
        if validate_legacy_accuracy:
            legacy_accuracy = legacy_accuracy_hints[block_index]
            if legacy_accuracy is not None and legacy_accuracy is not accuracy:
                raise AimsInputParsingError(
                    "historical control.in species accuracy does not match "
                    f"{species_name!r}"
                )
        normalized_source = _replace_embedded_species_name(
            block_text,
            species_name,
            element,
        )
        blocks.append(
            species_default_block_from_text(
                element,
                accuracy,
                PurePosixPath("/embedded/control.in")
                / accuracy.value
                / species_name,
                normalized_source,
            )
        )
    return InMemorySpeciesLibrary(blocks)


def _species_accuracy_hint(control: str) -> SpeciesAccuracy | None:
    suffix = control.split("# Species", 1)[1]
    metadata = _SPECIES_ACCURACY_METADATA.match(suffix)
    if metadata is not None:
        try:
            return SpeciesAccuracy(metadata.group("accuracy"))
        except ValueError:
            raise AimsInputParsingError(
                "generated control.in has unsupported species accuracy metadata"
            ) from None
    return None


def _legacy_species_accuracy_hints(
    body: str,
    declarations: tuple[re.Match[str], ...],
) -> tuple[SpeciesAccuracy | None, ...]:
    legacy_mapping = {
        "light": SpeciesAccuracy.LIGHT,
        "tight": SpeciesAccuracy.TIGHT,
        "safe": SpeciesAccuracy.REALLY_TIGHT,
    }
    hints_by_declaration: list[SpeciesAccuracy | None] = []
    previous_end = 0
    for declaration in declarations:
        header = body[previous_end : declaration.start()]
        hints = {
            legacy_mapping[match.group("name")]
            for match in _LEGACY_SPECIES_ACCURACY.finditer(header)
        }
        if len(hints) > 1:
            raise AimsInputParsingError(
                "historical control.in contains conflicting species accuracy "
                f"evidence for {declaration.group('name')!r}"
            )
        hints_by_declaration.append(next(iter(hints)) if hints else None)
        previous_end = declaration.end()
    return tuple(hints_by_declaration)


def _validate_embedded_species_names(
    control: str,
    expected_names: tuple[str, ...],
) -> None:
    suffix = control.split("# Species", 1)[1]
    actual_names = tuple(
        match.group("name")
        for match in _EMBEDDED_SPECIES_DECLARATION.finditer(suffix)
    )
    if actual_names != expected_names:
        raise AimsInputParsingError(
            "control.in embedded species declarations do not match geometry: "
            f"geometry={expected_names}, control={actual_names}"
        )


def _control_round_trip_matches(rendered: str, original: str) -> bool:
    if _SPECIES_ACCURACY_METADATA.search(original) is not None:
        return rendered == original
    without_new_metadata = _SPECIES_ACCURACY_METADATA.sub("", rendered, count=1)
    return without_new_metadata == original


def _replace_embedded_species_name(
    text: str,
    current_name: str,
    element: str,
) -> str:
    matches = tuple(_EMBEDDED_SPECIES_DECLARATION.finditer(text))
    if len(matches) != 1 or matches[0].group("name") != current_name:
        raise AimsInputParsingError(
            f"embedded species block {current_name!r} is malformed"
        )
    match = matches[0]
    return text[: match.start("name")] + element + text[match.end("name") :]


def _directives_before_species(control: str) -> tuple[str, ...]:
    marker = "# Species"
    if control.count(marker) != 1:
        raise AimsInputParsingError(
            "generated control.in must contain exactly one # Species section"
        )
    prefix = control.split(marker, 1)[0]
    return tuple(
        stripped
        for line in prefix.splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    )


def _one_value(lines: tuple[str, ...], keyword: str) -> str:
    prefix = keyword + " "
    matches = tuple(line[len(prefix) :] for line in lines if line.startswith(prefix))
    if len(matches) != 1:
        raise AimsInputParsingError(
            f"generated control.in requires exactly one {keyword} directive"
        )
    return matches[0]


def _geometry_metadata(
    geometry: str,
    structure: MolecularStructure,
) -> tuple[tuple[str, ...], dict[int, dict[str, float]]]:
    species_names: list[str] = []
    annotations: dict[int, dict[str, float]] = {}
    current_index: int | None = None
    for line_number, line in enumerate(geometry.splitlines(), start=1):
        fields = line.split()
        if not fields:
            continue
        if fields[0] == "atom":
            if len(fields) != 5:
                raise AimsInputParsingError(
                    f"geometry.in:{line_number} atom record is malformed"
                )
            current_index = len(species_names)
            species_names.append(fields[4])
            continue
        if fields[0] in {"initial_moment", "initial_charge"}:
            if current_index is None or len(fields) != 2:
                raise AimsInputParsingError(
                    f"geometry.in:{line_number} annotation is malformed"
                )
            bucket = annotations.setdefault(current_index, {})
            if fields[0] in bucket:
                raise AimsInputParsingError(
                    f"geometry.in:{line_number} duplicates {fields[0]}"
                )
            bucket[fields[0]] = _float(fields[1], fields[0])
            continue
        raise AimsInputParsingError(
            f"geometry.in:{line_number} contains unsupported generated content"
        )
    if len(species_names) != len(structure):
        raise AimsInputParsingError("geometry species metadata is incomplete")
    return tuple(species_names), annotations


def _optimization_atom_settings(
    structure: MolecularStructure,
    species_names: tuple[str, ...],
    annotations: dict[int, dict[str, float]],
    global_accuracy: SpeciesAccuracy,
) -> tuple[AtomAimsSettings, ...]:
    settings: list[AtomAimsSettings] = []
    for atom, species_name in zip(structure, species_names, strict=True):
        accuracy: SpeciesAccuracy | None = None
        if species_name != atom.element:
            matches = tuple(
                candidate
                for candidate in SpeciesAccuracy
                if species_name == f"{atom.element}_{candidate.value}"
            )
            if len(matches) != 1 or matches[0] is global_accuracy:
                raise AimsInputParsingError(
                    f"geometry species alias is not a generated accuracy override: "
                    f"{species_name!r}"
                )
            accuracy = matches[0]
        values = annotations.get(atom.index, {})
        moment = values.get("initial_moment")
        charge = values.get("initial_charge")
        if accuracy is not None or moment is not None or charge is not None:
            settings.append(
                AtomAimsSettings(atom.index, accuracy, moment, charge)
            )
    return tuple(settings)


def _optimization_spin(
    directives: tuple[str, ...],
    annotations: dict[int, dict[str, float]],
    spin_directive: str,
) -> SpinSettings:
    defaults = tuple(
        line.split(maxsplit=1)[1]
        for line in directives
        if line.startswith("default_initial_moment ")
    )
    fixed = tuple(
        line.split(maxsplit=1)[1]
        for line in directives
        if line.startswith("fixed_spin_moment ")
    )
    if len(defaults) > 1 or len(fixed) > 1:
        raise AimsInputParsingError("generated spin directives are duplicated")
    if spin_directive == "none":
        if defaults or fixed or any(
            "initial_moment" in values for values in annotations.values()
        ):
            raise AimsInputParsingError("spin-none input contains spin initialization")
        return SpinSettings()
    if spin_directive != "collinear":
        raise AimsInputParsingError(
            f"unsupported generated spin setting: {spin_directive!r}"
        )
    mode = (
        SpinInitializationMode.UNIFORM_DEFAULT
        if defaults
        else SpinInitializationMode.PER_ATOM
    )
    return SpinSettings(
        enabled=True,
        initialization_mode=mode,
        uniform_initial_moment=(
            _float(defaults[0], "default initial moment") if defaults else None
        ),
        fixed_spin_moment=(
            _float(fixed[0], "fixed spin moment") if fixed else None
        ),
    )


def _decode(value: str | bytes, source_name: str) -> str:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeError:
            raise AimsInputParsingError(
                f"{source_name} is not valid UTF-8"
            ) from None
    if not isinstance(value, str):
        raise TypeError(f"{source_name} must be text or bytes")
    return value


def _float(text: str, label: str) -> float:
    try:
        return float(text.replace("D", "E").replace("d", "e"))
    except (AttributeError, ValueError):
        raise AimsInputParsingError(f"{label} is not numeric") from None


def _integer(text: str, label: str) -> int:
    if re.fullmatch(r"[0-9]+", text) is None:
        raise AimsInputParsingError(f"{label} is not a positive integer")
    return int(text)


_EMBEDDED_SPECIES_DECLARATION = re.compile(
    r"^[ \t]*species[ \t]+(?P<name>[A-Za-z][A-Za-z0-9_]*)"
    r"[ \t]*(?:#.*)?$",
    re.MULTILINE,
)
_SPECIES_ACCURACY_METADATA = re.compile(
    r"\n# Moltage species accuracy: (?P<accuracy>[a-z_]+)"
)
_LEGACY_SPECIES_ACCURACY = re.compile(
    r"Suggested[ \t]+\"(?P<name>light|tight|safe)\"[ \t]+defaults"
)
