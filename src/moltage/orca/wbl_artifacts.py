"""Deterministic persisted artifacts for ORCA WBL results."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from hashlib import sha256
from io import StringIO
import json
from math import ceil, floor, isclose, isfinite, log10
from xml.sax.saxutils import escape

from moltage.orca.wbl import (
    WBL_MODEL_CLASSIFICATION,
    WBL_MODEL_ID,
    OrcaWblResult,
    WblSpinTreatment,
)


WBL_CSV_FILENAME = "orca_wbl_transmission.csv"
WBL_JSON_FILENAME = "orca_wbl_result.json"
WBL_SVG_FILENAME = "orca_wbl_transmission.svg"


class OrcaWblArtifactError(ValueError):
    """Raised when persisted WBL presentation artifacts are inconsistent."""


@dataclass(frozen=True, slots=True)
class OrcaWblPresentation:
    """Validated, display-sized WBL result reconstructed from JSON and CSV."""

    model_id: str
    model_classification: str
    energy_relative_ev: tuple[float, ...]
    energy_absolute_ev: tuple[float, ...]
    transmission_alpha: tuple[float, ...]
    transmission_beta: tuple[float, ...]
    transmission_total: tuple[float, ...]
    t_alpha_at_fermi: float | None
    t_beta_at_fermi: float | None
    t_total_at_fermi: float
    top_alpha: tuple[tuple[int, float], ...]
    top_beta: tuple[tuple[int, float], ...]
    source_hashes: tuple[tuple[str, str], ...]
    spin_treatment: WblSpinTreatment = WblSpinTreatment.SPIN_RESOLVED
    top_total: tuple[tuple[int, float], ...] = ()


def wbl_presentation_from_result(
    result: OrcaWblResult,
    *,
    source_hashes: dict[str, str],
) -> OrcaWblPresentation:
    """Create the local presentation paired with the just-persisted analysis."""

    if not isinstance(result, OrcaWblResult):
        raise TypeError("WBL presentation requires an OrcaWblResult")
    return OrcaWblPresentation(
        result.model_id,
        result.model_classification,
        result.energy_relative_ev,
        result.energy_absolute_ev,
        result.transmission_alpha,
        result.transmission_beta,
        result.transmission_total,
        result.t_alpha_at_fermi,
        result.t_beta_at_fermi,
        result.t_total_at_fermi,
        tuple(
            (item.mo_number, item.transmission_at_fermi)
            for item in result.top_alpha
        ),
        tuple(
            (item.mo_number, item.transmission_at_fermi)
            for item in result.top_beta
        ),
        tuple(
            sorted(
                (_plain_name(name), _sha256(digest))
                for name, digest in source_hashes.items()
            )
        ),
        result.spin_treatment,
        tuple(
            (item.mo_number, item.transmission_at_fermi)
            for item in result.top_total
        ),
    )


def parse_wbl_presentation(
    result_json: bytes,
    transmission_csv: bytes,
) -> OrcaWblPresentation:
    """Parse the two authoritative display artifacts without scientific fallback."""

    try:
        document = json.loads(result_json.decode("ascii"))
        if not isinstance(document, dict):
            raise TypeError("WBL result root must be an object")
        schema = document["schema"]
        if schema not in {
            "moltage.orca-wbl-result.v1",
            "moltage.orca-wbl-result.v2",
        }:
            raise ValueError("unsupported WBL result schema")
        model = document["model"]
        summary = document["summary"]
        source_hashes = document["source_sha256"]
        if not isinstance(model, dict) or not isinstance(summary, dict):
            raise TypeError("WBL model and summary must be objects")
        if not isinstance(source_hashes, dict):
            raise TypeError("WBL source hashes must be an object")
        if (
            model["id"] != WBL_MODEL_ID
            or model["classification"] != WBL_MODEL_CLASSIFICATION
        ):
            raise ValueError("unsupported WBL model identity")
        spin_treatment = (
            WblSpinTreatment.SPIN_RESOLVED
            if schema == "moltage.orca-wbl-result.v1"
            else WblSpinTreatment(model["spin_treatment"])
        )
        rows = tuple(csv.reader(StringIO(transmission_csv.decode("ascii"))))
        if spin_treatment is WblSpinTreatment.CLOSED_SHELL_SPIN_DEGENERATE:
            expected_header = (
                "energy_relative_ev",
                "energy_absolute_ev",
                "transmission_total",
            )
            expected_width = 3
        else:
            expected_header = (
                "energy_relative_ev",
                "energy_absolute_ev",
                "transmission_alpha",
                "transmission_beta",
                "transmission_total",
            )
            expected_width = 5
        if not rows or tuple(rows[0]) != expected_header:
            raise ValueError("WBL transmission CSV header is invalid")
        numeric_rows = tuple(
            tuple(float(value) for value in row) for row in rows[1:]
        )
        if not numeric_rows or any(
            len(row) != expected_width for row in numeric_rows
        ):
            raise ValueError("WBL transmission CSV rows are invalid")
        if any(not isfinite(value) for row in numeric_rows for value in row):
            raise ValueError("WBL transmission CSV contains non-finite values")
        if any(value < 0.0 for row in numeric_rows for value in row[2:]):
            raise ValueError("WBL transmission values must be non-negative")
        if spin_treatment is WblSpinTreatment.SPIN_RESOLVED and any(
            not isclose(
                row[4],
                row[2] + row[3],
                rel_tol=1.0e-11,
                abs_tol=1.0e-14,
            )
            for row in numeric_rows
        ):
            raise ValueError("WBL total curve differs from alpha plus beta")
        relative = tuple(row[0] for row in numeric_rows)
        if any(right <= left for left, right in zip(relative, relative[1:])):
            raise ValueError("WBL energy grid must be strictly increasing")
        total_fermi = _finite_nonnegative(
            summary["t_total_at_fermi"], "T_total(E_F)"
        )
        if spin_treatment is WblSpinTreatment.CLOSED_SHELL_SPIN_DEGENERATE:
            alpha_fermi = beta_fermi = None
            top_alpha = top_beta = ()
            top_total = _top_summary(summary["top_total"], "total")
            alpha = beta = ()
            total = tuple(row[2] for row in numeric_rows)
        else:
            alpha_fermi = _finite_nonnegative(
                summary["t_alpha_at_fermi"], "T_alpha(E_F)"
            )
            beta_fermi = _finite_nonnegative(
                summary["t_beta_at_fermi"], "T_beta(E_F)"
            )
            if not isclose(
                total_fermi,
                alpha_fermi + beta_fermi,
                rel_tol=1.0e-11,
                abs_tol=1.0e-14,
            ):
                raise ValueError("WBL Fermi summary differs from alpha plus beta")
            top_alpha = _top_summary(summary["top_alpha"], "alpha")
            top_beta = _top_summary(summary["top_beta"], "beta")
            top_total = ()
            alpha = tuple(row[2] for row in numeric_rows)
            beta = tuple(row[3] for row in numeric_rows)
            total = tuple(row[4] for row in numeric_rows)
        checked_hashes = tuple(
            sorted(
                (_plain_name(name), _sha256(digest))
                for name, digest in source_hashes.items()
            )
        )
        return OrcaWblPresentation(
            model["id"],
            model["classification"],
            relative,
            tuple(row[1] for row in numeric_rows),
            alpha,
            beta,
            total,
            alpha_fermi,
            beta_fermi,
            total_fermi,
            top_alpha,
            top_beta,
            checked_hashes,
            spin_treatment,
            top_total,
        )
    except OrcaWblArtifactError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError, csv.Error) as error:
        raise OrcaWblArtifactError(
            f"WBL result artifacts are malformed: {error}"
        ) from None


def _top_summary(value, spin: str) -> tuple[tuple[int, float], ...]:
    if not isinstance(value, list) or len(value) > 2:
        raise OrcaWblArtifactError(
            f"WBL {spin} top-orbital summary is invalid"
        )
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise OrcaWblArtifactError(
                f"WBL {spin} contribution must be an object"
            )
        number = item["mo_number_one_based"]
        contribution = _finite_nonnegative(
            item["transmission_at_fermi"],
            f"WBL {spin} contribution",
        )
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise OrcaWblArtifactError("WBL MO number must be a positive integer")
        result.append((number, contribution))
    return tuple(result)


def _finite_nonnegative(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OrcaWblArtifactError(f"{label} must be numeric")
    checked = float(value)
    if not isfinite(checked) or checked < 0.0:
        raise OrcaWblArtifactError(
            f"{label} must be finite and non-negative"
        )
    return checked


def _plain_name(value) -> str:
    if not isinstance(value, str) or not value or "/" in value or "\\" in value:
        raise OrcaWblArtifactError(
            "WBL source hash name must be a plain filename"
        )
    return value


def _sha256(value) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise OrcaWblArtifactError("WBL source SHA256 is invalid")
    return value


def render_wbl_csv(result: OrcaWblResult) -> bytes:
    stream = StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    if result.spin_treatment is WblSpinTreatment.CLOSED_SHELL_SPIN_DEGENERATE:
        writer.writerow(
            (
                "energy_relative_ev",
                "energy_absolute_ev",
                "transmission_total",
            )
        )
        rows = zip(
            result.energy_relative_ev,
            result.energy_absolute_ev,
            result.transmission_total,
            strict=True,
        )
    else:
        writer.writerow(
            (
                "energy_relative_ev",
                "energy_absolute_ev",
                "transmission_alpha",
                "transmission_beta",
                "transmission_total",
            )
        )
        rows = zip(
            result.energy_relative_ev,
            result.energy_absolute_ev,
            result.transmission_alpha,
            result.transmission_beta,
            result.transmission_total,
            strict=True,
        )
    for row in rows:
        writer.writerow(tuple(format(value, ".17g") for value in row))
    return stream.getvalue().encode("ascii")


def render_wbl_json(
    result: OrcaWblResult,
    *,
    source_hashes: dict[str, str],
    tool_evidence: dict[str, str],
) -> bytes:
    if result.spin_treatment is WblSpinTreatment.CLOSED_SHELL_SPIN_DEGENERATE:
        summary = {
            "t_total_at_fermi": result.t_total_at_fermi,
            "top_total": [_contribution(item) for item in result.top_total],
        }
        orbital_contributions = {
            "total": [
                _contribution(item)
                for item in result.total_contributions_at_fermi
            ]
        }
    else:
        summary = {
            "t_alpha_at_fermi": result.t_alpha_at_fermi,
            "t_beta_at_fermi": result.t_beta_at_fermi,
            "t_total_at_fermi": result.t_total_at_fermi,
            "top_alpha": [_contribution(item) for item in result.top_alpha],
            "top_beta": [_contribution(item) for item in result.top_beta],
        }
        orbital_contributions = {
            "alpha": [
                _contribution(item)
                for item in result.alpha_contributions_at_fermi
            ],
            "beta": [
                _contribution(item)
                for item in result.beta_contributions_at_fermi
            ],
        }
    document = {
        "schema": "moltage.orca-wbl-result.v2",
        "model": {
            "id": result.model_id,
            "classification": result.model_classification,
            "spin_treatment": result.spin_treatment.value,
            "transmission_convention": (
                "G_OVER_G0_WITH_G0_2E2_OVER_H"
                if result.spin_treatment
                is WblSpinTreatment.CLOSED_SHELL_SPIN_DEGENERATE
                else "RAW_ALPHA_PLUS_BETA_IN_E2_OVER_H_PER_SPIN_CONVENTION"
            ),
            "description": (
                "Linker-parameterized independent-resonance wide-band-limit "
                "hypothesis; not explicit Au-molecule-Au DFT-NEGF"
            ),
        },
        "settings": _settings(result),
        "contacts": {
            "left": _projector(result.left_projector),
            "right": _projector(result.right_projector),
        },
        "summary": summary,
        "orbital_contributions_at_fermi": orbital_contributions,
        "artifacts": {"transmission_csv": WBL_CSV_FILENAME, "plot_svg": WBL_SVG_FILENAME},
        "source_sha256": dict(sorted(source_hashes.items())),
        "tool_evidence": dict(sorted(tool_evidence.items())),
    }
    return (json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("ascii")


def render_wbl_svg(result: OrcaWblResult, *, width: int = 1200, height: int = 760) -> bytes:
    margin_left, margin_right, margin_top, margin_bottom = 105, 45, 55, 85
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom
    displayed_curves = (
        (("Total", result.transmission_total, "#111111", "", 2.5),)
        if result.spin_treatment is WblSpinTreatment.CLOSED_SHELL_SPIN_DEGENERATE
        else (
            ("Alpha", result.transmission_alpha, "#1976d2", "", 2.0),
            ("Beta", result.transmission_beta, "#d32f2f", ' stroke-dasharray="8 5"', 2.0),
            ("Spin sum", result.transmission_total, "#111111", "", 2.5),
        )
    )
    positive = tuple(
        value
        for _name, values, _color, _dash, _width in displayed_curves
        for value in values
        if value > 0.0
    )
    y_min_exponent = floor(log10(min(positive))) if positive else -12
    y_max_exponent = max(0, ceil(log10(max(positive)))) if positive else 0
    if y_min_exponent >= y_max_exponent:
        y_min_exponent = y_max_exponent - 1
    y_log_span = y_max_exponent - y_min_exponent
    x_min = result.energy_relative_ev[0]
    x_max = result.energy_relative_ev[-1]

    def path(values):
        points = []
        for energy, value in zip(result.energy_relative_ev, values, strict=True):
            if value <= 0.0:
                points.append(None)
                continue
            x = margin_left + (energy - x_min) / (x_max - x_min) * plot_width
            y = margin_top + (
                (y_max_exponent - log10(value)) / y_log_span
            ) * plot_height
            points.append((x, y))
        drawing = []
        segment_start = True
        for point in points:
            if point is None:
                segment_start = True
                continue
            x, y = point
            drawing.append(("M" if segment_start else "L") + f" {x:.3f} {y:.3f}")
            segment_start = False
        return " ".join(drawing)

    grid_and_labels = []
    for exponent in range(y_min_exponent, y_max_exponent + 1):
        y = margin_top + (y_max_exponent - exponent) / y_log_span * plot_height
        label = _power_of_ten_text(exponent)
        grid_and_labels.append(
            f'<line x1="{margin_left}" y1="{y:.3f}" x2="{margin_left + plot_width}" '
            f'y2="{y:.3f}" stroke="#dddddd" stroke-width="1"/>'
            f'<text x="{margin_left - 10}" y="{y + 5:.3f}" text-anchor="end" '
            f'font-family="Arial" font-size="14">{label}</text>'
        )
    curve_paths = "".join(
        f'<path d="{path(values)}" fill="none" stroke="{color}" '
        f'stroke-width="{stroke_width}"{dash}/>'
        for _name, values, color, dash, stroke_width in displayed_curves
    )
    legend_parts = []
    legend_width = 130 * len(displayed_curves)
    legend_x = width - margin_right - legend_width
    for index, (name, _values, color, dash, stroke_width) in enumerate(displayed_curves):
        x = legend_x + index * 130
        legend_parts.append(
            f'<line x1="{x}" y1="35" x2="{x + 35}" y2="35" stroke="{color}" '
            f'stroke-width="{stroke_width}"{dash}/>'
            f'<text x="{x + 42}" y="40">{escape(name)}</text>'
        )

    title = escape("ORCA linker-parameterized WBL transmission (HYPOTHESIS)")
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="white"/>
<text x="{width/2:.1f}" y="30" text-anchor="middle" font-family="Arial" font-size="20">{title}</text>
{''.join(grid_and_labels)}
<rect x="{margin_left}" y="{margin_top}" width="{plot_width}" height="{plot_height}" fill="none" stroke="black" stroke-width="1.5"/>
{curve_paths}
<line x1="{margin_left + (0.0-x_min)/(x_max-x_min)*plot_width:.3f}" y1="{margin_top}" x2="{margin_left + (0.0-x_min)/(x_max-x_min)*plot_width:.3f}" y2="{margin_top+plot_height}" stroke="#777" stroke-dasharray="4 4"/>
<text x="{width/2:.1f}" y="{height-25}" text-anchor="middle" font-family="Arial" font-size="17">E - E_F (eV)</text>
<text transform="translate(28 {height/2:.1f}) rotate(-90)" text-anchor="middle" font-family="Arial" font-size="17">Transmission</text>
<text x="{margin_left}" y="{height-50}" font-family="Arial" font-size="14">{x_min:.3g}</text>
<text x="{margin_left+plot_width}" y="{height-50}" text-anchor="end" font-family="Arial" font-size="14">{x_max:.3g}</text>
<g font-family="Arial" font-size="14">{''.join(legend_parts)}</g>
</svg>\n'''
    return svg.encode("utf-8")


def _power_of_ten_text(exponent: int) -> str:
    superscript = str(exponent).translate(
        str.maketrans(
            {
                "-": "⁻",
                "0": "⁰",
                "1": "¹",
                "2": "²",
                "3": "³",
                "4": "⁴",
                "5": "⁵",
                "6": "⁶",
                "7": "⁷",
                "8": "⁸",
                "9": "⁹",
            }
        )
    )
    return f"10{superscript}"


def render_wbl_artifacts(result, *, source_hashes, tool_evidence):
    csv_bytes = render_wbl_csv(result)
    svg_bytes = render_wbl_svg(result)
    json_bytes = render_wbl_json(
        result, source_hashes=source_hashes, tool_evidence=tool_evidence
    )
    contents = {
        WBL_CSV_FILENAME: csv_bytes,
        WBL_JSON_FILENAME: json_bytes,
        WBL_SVG_FILENAME: svg_bytes,
    }
    return contents, {name: sha256(data).hexdigest() for name, data in contents.items()}


def _settings(result):
    settings = result.settings
    return {
        "fermi_energy_ev": settings.fermi_energy_ev,
        "energy_min_relative_ev": settings.energy_min_relative_ev,
        "energy_max_relative_ev": settings.energy_max_relative_ev,
        "energy_step_ev": settings.energy_step_ev,
        "left": _contact(settings.left),
        "right": _contact(settings.right),
    }


def _contact(contact):
    return {
        "atom_index_zero_based": contact.atom_index,
        "linker": contact.linker.value,
        "gamma0_ev": contact.gamma0_ev,
        "parameter_status": contact.parameter_status.value,
        "requested_subspace_mode": contact.subspace_mode.value,
        "manual_direction": contact.manual_direction,
        "manual_ao_indices_zero_based": list(contact.manual_ao_indices),
    }


def _projector(projector):
    return {
        "atom_index_zero_based": projector.atom_index,
        "linker": projector.linker.value,
        "resolved_mode": projector.resolved_mode.value,
        "direction": projector.direction,
        "ao_indices_zero_based": list(projector.ao_indices),
        "projector_vectors": [
            [{"ao_index": index, "coefficient": coefficient} for index, coefficient in vector]
            for vector in projector.vectors
        ],
        "basis_mapping": projector.basis_mapping,
    }


def _contribution(item):
    return {
        "spin": item.spin,
        "mo_number_one_based": item.mo_number,
        "orbital_energy_ev": item.orbital_energy_ev,
        "left_weight": item.left_weight,
        "right_weight": item.right_weight,
        "gamma_left_ev": item.gamma_left_ev,
        "gamma_right_ev": item.gamma_right_ev,
        "transmission_at_fermi": item.transmission_at_fermi,
    }
