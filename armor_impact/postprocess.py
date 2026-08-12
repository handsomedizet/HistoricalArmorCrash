from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv
import json
import math
import re
import statistics
from typing import Any, Iterable


FLOAT_PATTERN = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+|[+-]\d{1,3})?"
TIME_AT_RE = re.compile(rf"at\s+time\s+({FLOAT_PATTERN})", re.IGNORECASE)
TIME_EQ_RE = re.compile(
    rf"^\s*time(?!\s+step)\s*(?:=|\.+)\s*({FLOAT_PATTERN})",
    re.IGNORECASE,
)
NODE_ROW_RE = re.compile(r"^\s*(\d+)\s+(.*)$")
INJURY_INPUT_SCHEMA_VERSION = "injury-prediction-input/v3"
INJURY_INPUT_FILENAME = "injury_prediction_input.json"
INJURY_INPUTS_FILENAME = "injury_prediction_inputs.jsonl"

UNIT_CONVENTION = {
    "field_units_encoded_in_names": True,
    "source_model_unit_system": "mm-ms-kg-GPa-kN-J",
    "source_model_unit_basis": (
        "declared consistently by the generated deck; LS-DYNA does not enforce a unit system"
    ),
    "velocity_note": "mm/ms is numerically equal to m/s",
    "postprocessing_conversions": {
        "acceleration_to_g": "acceleration_mm_ms2 * 1000 / 9.80665",
    },
    "dimensionless_fields": [
        "projectile_mass_scale",
        "projectile_kinetic_energy_loss_fraction",
        "projectile_speed_loss_fraction",
        "max_compression_ratio",
        "final_energy_ratio",
        "final_energy_ratio_without_eroded",
        "final_hourglass_to_internal_ratio",
    ],
}

METRIC_DEFINITIONS = {
    "deflection": {
        "formula": "front_global_y_displacement_mm - back_global_y_displacement_mm",
        "peak_rule": "maximum value clipped at zero",
        "filtering": "none",
    },
    "compression_ratio": {
        "formula": "signed_deflection_mm / initial_torso_depth_mm",
        "reference_dimension": "model_context.surrogate_geometry.depth_mm",
        "peak_rule": "maximum value clipped at zero",
    },
    "vc": {
        "formula": "max(0, compression_ratio * d(deflection_mm)/dt_ms)",
        "differentiation": "central difference internally; one-sided difference at endpoints",
        "sign_convention": "only positive ratio-times-compression-velocity is retained",
        "filtering": "none",
        "validation_scope": "screening proxy for this surrogate; not a validated human VC criterion",
    },
    "torso_center_acceleration": {
        "raw_peak": "magnitude of LS-DYNA NODOUT acceleration at one center node",
        "vector_average_3ms_peak": (
            "maximum magnitude of delta nodal velocity over sliding 3 ms windows, "
            "with linear interpolation at each window endpoint"
        ),
        "preferred_screening_metric": "vector_average_3ms_peak_g",
    },
}


@dataclass(frozen=True)
class NodeState:
    displacement_mm: tuple[float, float, float]
    velocity_mps: tuple[float, float, float]
    acceleration_mm_ms2: tuple[float, float, float]
    coordinate_mm: tuple[float, float, float]


@dataclass(frozen=True)
class NodalFrame:
    time_ms: float
    nodes: dict[int, NodeState]


@dataclass(frozen=True)
class EnergyFrame:
    time_ms: float
    values: dict[str, float]


def parse_lsdyna_float(token: str) -> float:
    value = token.strip().replace("D", "E").replace("d", "e")
    if "e" not in value.lower():
        match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))([+-]\d{1,3})", value)
        if match:
            value = f"{match.group(1)}e{match.group(2)}"
    return float(value)


def parse_nodout(path: str | Path) -> list[NodalFrame]:
    frames: list[NodalFrame] = []
    current_time: float | None = None
    current_nodes: dict[int, NodeState] = {}

    def flush() -> None:
        nonlocal current_time, current_nodes
        if current_time is not None and current_nodes:
            frames.append(NodalFrame(current_time, current_nodes))
        current_nodes = {}

    with Path(path).open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            time_match = TIME_AT_RE.search(line)
            if time_match:
                flush()
                current_time = parse_lsdyna_float(time_match.group(1))
                continue
            if current_time is None:
                continue
            row_match = NODE_ROW_RE.match(line)
            if row_match is None:
                continue
            number_tokens = re.findall(FLOAT_PATTERN, row_match.group(2))
            if len(number_tokens) < 12:
                continue
            try:
                node_id = int(row_match.group(1))
                values = [parse_lsdyna_float(item) for item in number_tokens[:12]]
            except ValueError:
                continue
            current_nodes[node_id] = NodeState(
                displacement_mm=tuple(values[0:3]),  # type: ignore[arg-type]
                velocity_mps=tuple(values[3:6]),  # type: ignore[arg-type]
                acceleration_mm_ms2=tuple(values[6:9]),  # type: ignore[arg-type]
                coordinate_mm=tuple(values[9:12]),  # type: ignore[arg-type]
            )
    flush()
    return frames


ENERGY_LABELS = {
    "eroded kinetic energy": "eroded_kinetic_energy_j",
    "eroded internal energy": "eroded_internal_energy_j",
    "eroded hourglass energy": "eroded_hourglass_energy_j",
    "kinetic energy": "kinetic_energy_j",
    "internal energy": "internal_energy_j",
    "hourglass energy": "hourglass_energy_j",
    "sliding interface energy": "sliding_energy_j",
    "system damping energy": "damping_energy_j",
    "external work": "external_work_j",
    "total energy / initial energy": "energy_ratio",
    "energy ratio w/o eroded energy": "energy_ratio_without_eroded",
    "total energy": "total_energy_j",
}


def parse_glstat(path: str | Path) -> list[EnergyFrame]:
    frames: list[EnergyFrame] = []
    current_time: float | None = None
    values: dict[str, float] = {}

    def flush() -> None:
        nonlocal values
        if current_time is not None and values:
            frames.append(EnergyFrame(current_time, dict(values)))
        values = {}

    labels = sorted(ENERGY_LABELS, key=len, reverse=True)
    with Path(path).open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            time_match = TIME_EQ_RE.search(line)
            if time_match and "time step" not in line.lower():
                flush()
                current_time = parse_lsdyna_float(time_match.group(1))
                continue
            lowered = " ".join(line.lower().split())
            for label in labels:
                match = re.match(
                    rf"^{re.escape(label)}\s*(?:=|\.+)\s*({FLOAT_PATTERN})",
                    lowered,
                )
                if match:
                    values[ENERGY_LABELS[label]] = parse_lsdyna_float(match.group(1))
                    break
    flush()
    return frames


def _node_series(
    frames: Iterable[NodalFrame],
    node_id: int,
    attribute: str,
    component: int,
    *,
    max_time_ms: float | None = None,
) -> tuple[list[float], list[float]]:
    times: list[float] = []
    values: list[float] = []
    for frame in frames:
        if max_time_ms is not None and frame.time_ms > max_time_ms:
            continue
        state = frame.nodes.get(node_id)
        if state is None:
            continue
        vector = getattr(state, attribute)
        times.append(frame.time_ms)
        values.append(float(vector[component]))
    return times, values


def _aligned_difference(
    frames: Iterable[NodalFrame],
    front_id: int,
    back_id: int,
    component: int = 1,
    *,
    max_time_ms: float | None = None,
) -> tuple[list[float], list[float]]:
    times: list[float] = []
    values: list[float] = []
    for frame in frames:
        if max_time_ms is not None and frame.time_ms > max_time_ms:
            continue
        front = frame.nodes.get(front_id)
        back = frame.nodes.get(back_id)
        if front is None or back is None:
            continue
        times.append(frame.time_ms)
        values.append(front.displacement_mm[component] - back.displacement_mm[component])
    return times, values


def _gradient(values: list[float], times_ms: list[float]) -> list[float]:
    if len(values) < 2:
        return [0.0] * len(values)
    result: list[float] = []
    for i in range(len(values)):
        if i == 0:
            dv, dt = values[1] - values[0], times_ms[1] - times_ms[0]
        elif i == len(values) - 1:
            dv, dt = values[-1] - values[-2], times_ms[-1] - times_ms[-2]
        else:
            dv, dt = values[i + 1] - values[i - 1], times_ms[i + 1] - times_ms[i - 1]
        result.append(dv / dt if dt else 0.0)
    # mm/ms is numerically equal to m/s.
    return result


def _acceleration_metrics(
    frames: Iterable[NodalFrame], node_id: int, *, max_time_ms: float | None = None
) -> dict[str, float | None]:
    states: list[tuple[float, NodeState]] = []
    for frame in frames:
        if max_time_ms is not None and frame.time_ms > max_time_ms:
            continue
        state = frame.nodes.get(node_id)
        if state is None:
            continue
        states.append((frame.time_ms, state))

    raw_peaks: list[float] = []
    for _, state in states:
        a = state.acceleration_mm_ms2
        magnitude_mm_ms2 = math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)
        raw_peaks.append(magnitude_mm_ms2 * 1000.0 / 9.80665)

    clip_ms = 3.0
    clip_peaks: list[float] = []
    end_index = 1
    for start_index, (start_time, start_state) in enumerate(states):
        target_time = start_time + clip_ms
        while end_index < len(states) and states[end_index][0] < target_time:
            end_index += 1
        if end_index >= len(states):
            break
        before_time, before_state = states[end_index - 1]
        after_time, after_state = states[end_index]
        if after_time == before_time:
            continue
        fraction = (target_time - before_time) / (after_time - before_time)
        end_velocity = tuple(
            before_state.velocity_mps[i]
            + fraction * (after_state.velocity_mps[i] - before_state.velocity_mps[i])
            for i in range(3)
        )
        delta_velocity = tuple(
            end_velocity[i] - start_state.velocity_mps[i] for i in range(3)
        )
        average_mm_ms2 = math.sqrt(sum(value ** 2 for value in delta_velocity)) / clip_ms
        clip_peaks.append(average_mm_ms2 * 1000.0 / 9.80665)

    raw_peak = max(raw_peaks) if raw_peaks else None
    clip_peak = max(clip_peaks) if clip_peaks else None
    return {
        "peak_raw_g": raw_peak,
        "peak_3ms_g": clip_peak,
        "preferred_peak_g": clip_peak if clip_peak is not None else raw_peak,
    }


def _residual_speed_metrics(
    frames: list[NodalFrame],
    node_id: int,
    deletion_time_ms: float | None,
    tracked_element_failure_time_ms: float | None,
) -> dict[str, object]:
    samples: list[tuple[float, float]] = []
    for frame in frames:
        state = frame.nodes.get(node_id)
        if state is None:
            continue
        v = state.velocity_mps
        samples.append((frame.time_ms, math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)))
    if deletion_time_ms is not None or tracked_element_failure_time_ms is not None:
        return {
            "speed_mps": None,
            "status": (
                "invalid_projectile_center_node_eroded"
                if deletion_time_ms is not None
                else "invalid_tracked_projectile_element_failed"
            ),
            "tail_fraction": 0.10,
            "tail_frame_count": 0,
            "window_start_ms": None,
            "window_end_ms": None,
            "sensor_deletion_time_ms": deletion_time_ms,
            "tracked_element_failure_time_ms": tracked_element_failure_time_ms,
        }
    if not samples:
        return {
            "speed_mps": None,
            "status": "not_available_no_projectile_center_history",
            "tail_fraction": 0.10,
            "tail_frame_count": 0,
            "window_start_ms": None,
            "window_end_ms": None,
            "sensor_deletion_time_ms": None,
            "tracked_element_failure_time_ms": None,
        }
    tail_size = max(1, len(samples) // 10)
    tail = samples[-tail_size:]
    return {
        "speed_mps": statistics.median(speed for _, speed in tail),
        "status": "computed",
        "tail_fraction": 0.10,
        "tail_frame_count": len(tail),
        "window_start_ms": tail[0][0],
        "window_end_ms": tail[-1][0],
        "sensor_deletion_time_ms": None,
        "tracked_element_failure_time_ms": None,
    }


def _pair_metrics(
    frames: list[NodalFrame],
    front: int,
    back: int,
    depth_mm: float,
    *,
    max_time_ms: float | None = None,
) -> tuple[dict[str, object], list[dict[str, float]]]:
    times, compression = _aligned_difference(
        frames, front, back, max_time_ms=max_time_ms
    )
    if not compression:
        return {
            "max_deflection_mm": None,
            "max_compression_ratio": None,
            "peak_vc_mps": None,
            "time_of_max_deflection_ms": None,
            "time_of_peak_vc_ms": None,
        }, []
    compression_velocity = _gradient(compression, times)
    ratios = [value / depth_mm for value in compression]
    vc = [max(0.0, ratio * velocity) for ratio, velocity in zip(ratios, compression_velocity)]
    history = [
        {
            "time_ms": t,
            "deflection_mm": c,
            "compression_ratio": r,
            "deflection_velocity_mps": v,
            "vc_mps": vc_value,
        }
        for t, c, r, v, vc_value in zip(times, compression, ratios, compression_velocity, vc)
    ]
    max_deflection = max(max(compression), 0.0)
    max_deflection_index = compression.index(max_deflection) if max_deflection > 0 else 0
    peak_vc = max(vc)
    peak_vc_index = vc.index(peak_vc)
    return {
        "max_deflection_mm": max_deflection,
        "max_compression_ratio": max(max(ratios), 0.0),
        "peak_vc_mps": peak_vc,
        "time_of_max_deflection_ms": times[max_deflection_index],
        "time_of_peak_vc_ms": times[peak_vc_index],
    }, history


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _region_payload(metrics: dict[str, object], key: str) -> dict[str, object]:
    values = _as_dict(metrics.get(key))
    return {
        "measurement_basis": "paired_single_front_and_back_surface_nodes",
        "front_node_id": values.get("front_node_id"),
        "back_node_id": values.get("back_node_id"),
        "measurement_validity": values.get("measurement_validity"),
        "measurement_valid_through_ms": values.get("measurement_valid_through_ms"),
        "sensor_deletion_time_ms": values.get("sensor_deletion_time_ms"),
        "tracked_element_id": values.get("tracked_element_id"),
        "tracked_element_failure_time_ms": values.get("tracked_element_failure_time_ms"),
        "tracked_element_monitoring_status": values.get("tracked_element_monitoring_status"),
        "max_deflection_mm": values.get("max_deflection_mm"),
        "max_compression_ratio": values.get("max_compression_ratio"),
        "peak_vc_mps": values.get("peak_vc_mps"),
        "time_of_max_deflection_ms": values.get("time_of_max_deflection_ms"),
        "time_of_peak_vc_ms": values.get("time_of_peak_vc_ms"),
    }


def _node_deletion_time(case_dir: Path, node_id: int) -> float | None:
    pattern = re.compile(
        rf"node\s+number\s+{node_id}\s+deleted\s+at\s+time\s+({FLOAT_PATTERN})",
        re.IGNORECASE,
    )
    times: list[float] = []
    for name in ("solver.log", "messag"):
        path = case_dir / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        times.extend(parse_lsdyna_float(match.group(1)) for match in pattern.finditer(text))
    return min(times) if times else None


def _element_failure_time(case_dir: Path, element_id: int) -> float | None:
    pattern = re.compile(
        rf"(?:shell|solid)\s+element\s+{element_id}\s+failed\s+at\s+time\s+({FLOAT_PATTERN})",
        re.IGNORECASE,
    )
    times: list[float] = []
    for name in ("solver.log", "messag"):
        path = case_dir / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        times.extend(parse_lsdyna_float(match.group(1)) for match in pattern.finditer(text))
    return min(times) if times else None


def _initial_node_coordinate(frames: Iterable[NodalFrame], node_id: int) -> list[float] | None:
    for frame in frames:
        state = frame.nodes.get(node_id)
        if state is not None:
            return list(state.coordinate_mm)
    return None


def _value_at(payload: dict[str, object], path: tuple[str, ...]) -> object:
    value: object = payload
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _validate_injury_prediction_input(payload: dict[str, object]) -> dict[str, object]:
    quality = _as_dict(payload.get("simulation_quality"))
    quality_warnings = [str(value) for value in _as_list(quality.get("warnings"))]
    validation_warnings: list[str] = []
    validation_errors: list[str] = []

    def sanitize(value: object, path: str) -> object:
        if isinstance(value, float) and not math.isfinite(value):
            validation_errors.append(f"Non-finite value replaced with None at {path}.")
            return None
        if isinstance(value, dict):
            for key, child in list(value.items()):
                value[key] = sanitize(child, f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                value[index] = sanitize(child, f"{path}[{index}]")
        return value

    sanitize(payload, "")

    required_paths = (
        ("impact_conditions", "projectile_mass_kg"),
        ("impact_conditions", "impact_speed_mps"),
        ("impact_conditions", "projectile_initial_ke_j"),
        ("projectile_response", "projectile_residual_speed_mps"),
        ("projectile_response", "projectile_residual_ke_j"),
        ("projectile_response", "projectile_kinetic_energy_loss_j"),
        ("torso_response", "impact_site", "max_deflection_mm"),
        ("torso_response", "impact_site", "max_compression_ratio"),
        ("torso_response", "impact_site", "peak_vc_mps"),
        ("torso_response", "chest", "max_deflection_mm"),
        ("torso_response", "chest", "max_compression_ratio"),
        ("torso_response", "chest", "peak_vc_mps"),
        ("torso_response", "abdomen", "max_deflection_mm"),
        ("torso_response", "abdomen", "max_compression_ratio"),
        ("torso_response", "abdomen", "peak_vc_mps"),
        ("torso_response", "torso_center_acceleration", "raw_peak_g"),
        ("simulation_quality", "simulation_duration_ms"),
        ("simulation_quality", "final_internal_energy_j"),
        ("simulation_quality", "final_hourglass_energy_j"),
        ("simulation_quality", "final_energy_ratio"),
    )
    for path in required_paths:
        if not _finite_number(_value_at(payload, path)):
            validation_errors.append(f"Required feature is unavailable: {'.'.join(path)}.")

    armor_type = _value_at(payload, ("impact_conditions", "armor_type"))
    if armor_type != "none" and not _finite_number(
        _value_at(payload, ("armor_response", "armor_peak_ap_displacement_mm"))
    ):
        validation_errors.append(
            "Required feature is unavailable: armor_response.armor_peak_ap_displacement_mm."
        )

    if _value_at(payload, ("simulation_quality", "nodout_parse_status")) != "parsed":
        validation_errors.append("NODOUT was not parsed successfully.")
    if _value_at(payload, ("simulation_quality", "glstat_parse_status")) != "parsed":
        validation_errors.append("GLSTAT was not parsed successfully.")

    positive_paths = (
        ("impact_conditions", "projectile_mass_kg"),
        ("impact_conditions", "projectile_mass_scale"),
    )
    for path in positive_paths:
        value = _value_at(payload, path)
        if _finite_number(value) and float(value) <= 0:
            validation_errors.append(f"Expected a positive value at {'.'.join(path)}.")

    nonnegative_paths = (
        ("impact_conditions", "projectile_initial_ke_j"),
        ("projectile_response", "projectile_residual_ke_j"),
    )
    for path in nonnegative_paths:
        value = _value_at(payload, path)
        if _finite_number(value) and float(value) < 0:
            validation_errors.append(f"Expected a nonnegative value at {'.'.join(path)}.")

    torso = _as_dict(payload.get("torso_response"))
    for region_name in ("impact_site", "chest", "abdomen"):
        region = _as_dict(torso.get(region_name))
        ratio = region.get("max_compression_ratio")
        if _finite_number(ratio) and not 0.0 <= float(ratio) <= 1.0:
            validation_errors.append(
                f"Compression ratio outside [0, 1] at torso_response.{region_name}."
            )

    duration = quality.get("simulation_duration_ms")

    def check_times(value: object, path: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else key
                if (
                    isinstance(child, (int, float))
                    and not isinstance(child, bool)
                    and key.endswith("_ms")
                    and any(token in key for token in ("time", "window", "duration", "through"))
                ):
                    if float(child) < 0:
                        validation_errors.append(f"Negative time value at {child_path}.")
                    elif (
                        key != "simulation_duration_ms"
                        and _finite_number(duration)
                        and float(child) > float(duration) + 1.0e-6
                    ):
                        validation_errors.append(
                            f"Time value exceeds simulation duration at {child_path}."
                        )
                check_times(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                check_times(child, f"{path}[{index}]")

    check_times(payload)

    prediction = _as_dict(payload.get("prediction_result"))
    if prediction.get("status") == "not_scored" and (
        prediction.get("injury_probability") is not None
        or prediction.get("injury_severity") is not None
    ):
        validation_errors.append(
            "prediction_result is not_scored but contains a probability or severity."
        )

    energy_loss = _value_at(
        payload, ("projectile_response", "projectile_kinetic_energy_loss_j")
    )
    if _finite_number(energy_loss) and float(energy_loss) < 0:
        validation_warnings.append(
            "Projectile kinetic-energy loss is negative; the residual proxy speed exceeds impact speed."
        )

    armor = _as_dict(payload.get("armor_response"))
    if armor.get("armor_perforation_detected") is True and not armor.get(
        "armor_perforation_evidence"
    ):
        validation_errors.append(
            "Armor perforation is true without an explicit determination basis."
        )
    if armor.get("displacement_history_scope") == "pre_local_failure":
        peak_time = armor.get("armor_peak_ap_displacement_time_ms")
        failure_time = armor.get("armor_local_failure_time_ms")
        if (
            _finite_number(peak_time)
            and _finite_number(failure_time)
            and float(peak_time) > float(failure_time) + 1.0e-6
        ):
            validation_errors.append(
                "Armor displacement peak occurs after the declared local-failure cutoff."
            )

    raw_acceleration = _value_at(
        payload, ("torso_response", "torso_center_acceleration", "raw_peak_g")
    )
    averaged_acceleration = _value_at(
        payload,
        ("torso_response", "torso_center_acceleration", "vector_average_3ms_peak_g"),
    )
    if (
        _finite_number(raw_acceleration)
        and _finite_number(averaged_acceleration)
        and float(raw_acceleration) > 5.0 * max(float(averaged_acceleration), 1.0e-12)
    ):
        validation_warnings.append(
            "Raw center-node acceleration is more than five times the 3 ms vector-average peak."
        )

    for warning in validation_warnings:
        if warning not in quality_warnings:
            quality_warnings.append(warning)
    quality["validation_status"] = (
        "failed" if validation_errors else "passed_with_warnings" if validation_warnings else "passed"
    )
    quality["validation_errors"] = validation_errors
    quality["validation_warnings"] = validation_warnings
    quality["warnings"] = quality_warnings
    payload["simulation_quality"] = quality
    payload["injury_prediction_ready"] = bool(payload.get("injury_prediction_ready")) and not validation_errors
    return payload


def build_injury_prediction_input(metadata: dict[str, object], metrics: dict[str, object]) -> dict[str, object]:
    """Return the compact, unit-labeled payload intended for downstream AI scoring."""
    case = _as_dict(metadata.get("case"))
    body_dimensions = _as_dict(metadata.get("body_dimensions_mm"))
    if not body_dimensions and metadata.get("body_depth_mm") is not None:
        body_dimensions = {"depth_mm": metadata.get("body_depth_mm")}
    body_material = {
        key: value
        for key, value in _as_dict(metadata.get("body_material")).items()
        if key not in {"width_mm", "depth_mm", "height_mm"}
    }
    projectile_material = _as_dict(metadata.get("projectile_material"))
    projectile_material = dict(projectile_material)
    projectile_nominal_mass = metadata.get("projectile_nominal_mass_kg")
    projectile_mass_scale = metadata.get("projectile_mass_scale")
    projectile_effective_density = metadata.get("projectile_effective_density_kg_m3")
    caliber = metrics.get("caliber_mm", case.get("caliber_mm"))
    projectile_mass = metrics.get("projectile_mass_kg")
    nominal_density = projectile_material.get("density_kg_m3")
    if (
        not _finite_number(projectile_nominal_mass)
        and _finite_number(caliber)
        and _finite_number(nominal_density)
    ):
        radius_m = float(caliber) / 2000.0
        projectile_nominal_mass = (
            float(nominal_density) * (4.0 / 3.0) * math.pi * radius_m ** 3
        )
    if (
        not _finite_number(projectile_mass_scale)
        and _finite_number(projectile_mass)
        and _finite_number(projectile_nominal_mass)
        and float(projectile_nominal_mass) > 0
    ):
        projectile_mass_scale = float(projectile_mass) / float(projectile_nominal_mass)
    if (
        not _finite_number(projectile_effective_density)
        and _finite_number(nominal_density)
        and _finite_number(projectile_mass_scale)
    ):
        projectile_effective_density = float(nominal_density) * float(projectile_mass_scale)
    nominal_density_value = projectile_material.pop("density_kg_m3", nominal_density)
    projectile_material["nominal_density_kg_m3"] = nominal_density_value
    limitations = list(_as_list(metadata.get("model_limitations")))
    if _finite_number(projectile_mass_scale) and abs(float(projectile_mass_scale) - 1.0) > 0.01:
        density_warning = (
            "Requested projectile mass differs from the nominal spherical mass; "
            f"projectile density is scaled by {float(projectile_mass_scale):.6g}."
        )
        if density_warning not in limitations:
            limitations.append(density_warning)
    initial_ke = metrics.get("projectile_initial_ke_j")
    kinetic_energy_loss = metrics.get(
        "projectile_kinetic_energy_loss_j", metrics.get("projectile_energy_change_j")
    )
    kinetic_energy_loss_fraction = None
    if (
        _finite_number(initial_ke)
        and float(initial_ke) > 0
        and _finite_number(kinetic_energy_loss)
    ):
        kinetic_energy_loss_fraction = float(kinetic_energy_loss) / float(initial_ke)

    initial_speed = metrics.get("impact_speed_mps")
    residual_speed = metrics.get("projectile_residual_speed_mps")
    speed_loss = None
    speed_loss_fraction = None
    if _finite_number(initial_speed) and _finite_number(residual_speed):
        speed_loss = float(initial_speed) - float(residual_speed)
        if float(initial_speed) > 0:
            speed_loss_fraction = speed_loss / float(initial_speed)

    armor_type = metrics.get("armor_type", case.get("armor_type"))
    residual_measurement = _as_dict(metrics.get("projectile_residual_measurement"))
    payload: dict[str, object] = {
        "schema_version": INJURY_INPUT_SCHEMA_VERSION,
        "case_id": metrics.get("case_id", metadata.get("case_id")),
        "run_id": metadata.get("run_id", metrics.get("case_id", metadata.get("case_id"))),
        "prediction_task": "thoracoabdominal_injury_risk_screening",
        "prediction_result": {
            "status": "not_scored",
            "injury_probability": None,
            "injury_severity": None,
            "note": "Simulation features are ready; downstream AI scoring has not been performed.",
        },
        "model_context": {
            "model_type": "homogeneous_viscoelastic_torso_surrogate",
            "screening_only": bool(metrics.get("screening_only", True)),
            "unit_convention": UNIT_CONVENTION,
            "surrogate_geometry": body_dimensions,
            "body_material": body_material,
            "armor_geometry": _as_dict(metadata.get("armor_geometry")),
            "armor_material": _as_dict(metadata.get("armor_material")),
            "projectile_material": projectile_material,
            "metric_definitions": METRIC_DEFINITIONS,
            "measurement_provenance": {
                "nodout": {
                    "source_file": "nodout",
                    "quantities": "selected nodal displacement, velocity, acceleration, and coordinate histories",
                    "regional_selection": {
                        "impact_site": "nearest structured-mesh front/back nodes to requested impact x/z",
                        "chest": "nearest front/back nodes to x=0 and z=+0.20*torso height",
                        "abdomen": "nearest front/back nodes to x=0 and z=-0.20*torso height",
                    },
                },
                "glstat": {
                    "source_file": "glstat",
                    "scope": "global system energy history; final parsed frame is reported",
                },
                "failure_events": {
                    "source_files": ["solver.log", "messag"],
                    "basis": "explicit node-deletion and element-failure messages",
                },
            },
            "limitations": limitations,
        },
        "impact_conditions": {
            "armor_type": armor_type,
            "caliber_mm": caliber,
            "impact_speed_mps": initial_speed,
            "yaw_deg": metrics.get("yaw_deg", case.get("yaw_deg")),
            "pitch_deg": metrics.get("pitch_deg", case.get("pitch_deg")),
            "impact_x_mm": metrics.get("impact_x_mm", case.get("impact_x_mm")),
            "impact_z_mm": metrics.get("impact_z_mm", case.get("impact_z_mm")),
            "mesh_scale": metrics.get("mesh_scale", case.get("mesh_scale")),
            "projectile_mass_kg": projectile_mass,
            "projectile_nominal_mass_kg": projectile_nominal_mass,
            "projectile_mass_scale": projectile_mass_scale,
            "projectile_effective_density_kg_m3": projectile_effective_density,
            "projectile_initial_ke_j": initial_ke,
        },
        "projectile_response": {
            "projectile_residual_speed_mps": residual_speed,
            "projectile_residual_ke_j": metrics.get("projectile_residual_ke_j"),
            "projectile_kinetic_energy_loss_j": kinetic_energy_loss,
            "projectile_kinetic_energy_loss_fraction": kinetic_energy_loss_fraction,
            "projectile_speed_loss_mps": speed_loss,
            "projectile_speed_loss_fraction": speed_loss_fraction,
            "residual_measurement": {
                "status": residual_measurement.get("status"),
                "basis": "median velocity magnitude of the projectile center mesh node over the final 10% of parsed NODOUT frames",
                "sensor_basis": "single_projectile_center_mesh_node_not_center_of_mass",
                "sensor_node_id": _as_dict(metadata.get("sensors")).get("projectile_center"),
                "tail_fraction": residual_measurement.get("tail_fraction"),
                "tail_frame_count": residual_measurement.get("tail_frame_count"),
                "window_start_ms": residual_measurement.get("window_start_ms"),
                "window_end_ms": residual_measurement.get("window_end_ms"),
                "sensor_deletion_time_ms": residual_measurement.get(
                    "sensor_deletion_time_ms"
                ),
                "tracked_element_failure_time_ms": residual_measurement.get(
                    "tracked_element_failure_time_ms"
                ),
                "derived_energy_basis": (
                    "0.5 * requested_projectile_mass_kg * residual_proxy_speed_mps^2"
                ),
            },
        },
        "armor_response": {
            "armor_peak_ap_displacement_mm": metrics.get("armor_peak_ap_displacement_mm"),
            "armor_peak_ap_displacement_time_ms": metrics.get(
                "armor_peak_ap_displacement_time_ms"
            ),
            "armor_local_failure_detected": (
                None if armor_type == "none" else metrics.get("armor_local_failure_detected")
            ),
            "armor_local_failure_time_ms": metrics.get("armor_local_failure_time_ms"),
            "armor_local_failure_detection_basis": metrics.get(
                "armor_local_failure_detection_basis"
            ),
            "armor_sensor_deletion_time_ms": metrics.get("armor_sensor_deletion_time_ms"),
            "displacement_history_scope": metrics.get("armor_displacement_history_scope"),
            "displacement_measurement": {
                "basis": "global Y displacement of one armor node nearest the requested impact location",
                "direction": "global_positive_y_anterior_to_posterior",
                "reference": "initial_node_position",
                "aggregation": "single_node_not_node_set_maximum",
                "sensor_node_id": _as_dict(metadata.get("sensors")).get("armor_near_impact"),
                "rigid_body_translation_removed": False,
                "measurement_valid_through_ms": metrics.get(
                    "armor_displacement_measurement_end_time_ms"
                ),
            },
            "armor_perforation_detected": None,
            "armor_perforation_status": (
                "not_applicable_no_armor"
                if armor_type == "none"
                else "not_determined_from_available_histories"
            ),
            "armor_perforation_evidence": None,
        },
        "torso_response": {
            "impact_site": _region_payload(metrics, "impact_site"),
            "chest": _region_payload(metrics, "chest"),
            "abdomen": _region_payload(metrics, "abdomen"),
            "torso_center_acceleration": {
                "sensor_basis": "single_center_node_not_torso_center_of_mass",
                "sensor_node_id": _as_dict(metadata.get("sensors")).get("torso_center"),
                "measurement_validity": metrics.get("torso_acceleration_measurement_validity"),
                "measurement_valid_through_ms": metrics.get(
                    "torso_acceleration_measurement_end_time_ms"
                ),
                "sensor_deletion_time_ms": metrics.get(
                    "torso_acceleration_sensor_deletion_time_ms"
                ),
                "raw_peak_g": metrics.get("torso_center_peak_acceleration_raw_g"),
                "vector_average_3ms_peak_g": metrics.get(
                    "torso_center_peak_acceleration_3ms_g"
                ),
                "preferred_screening_metric": metrics.get(
                    "torso_center_acceleration_preferred_metric"
                ),
            },
        },
        "simulation_quality": {
            "analysis_status": metrics.get("analysis_status"),
            "simulation_duration_ms": metrics.get("simulation_duration_ms"),
            "nodout_parse_status": metrics.get("nodout_parse_status"),
            "glstat_parse_status": metrics.get("glstat_parse_status"),
            "energy_accounting_basis": "final parsed global LS-DYNA GLSTAT frame",
            "energy_field_status": metrics.get("energy_field_status"),
            "final_internal_energy_j": metrics.get("final_internal_energy_j"),
            "final_hourglass_energy_j": metrics.get("final_hourglass_energy_j"),
            "final_eroded_kinetic_energy_j": metrics.get("final_eroded_kinetic_energy_j"),
            "final_eroded_internal_energy_j": metrics.get("final_eroded_internal_energy_j"),
            "final_eroded_hourglass_energy_j": metrics.get("final_eroded_hourglass_energy_j"),
            "final_sliding_energy_j": metrics.get("final_sliding_energy_j"),
            "final_energy_ratio": metrics.get("final_energy_ratio"),
            "final_energy_ratio_without_eroded": metrics.get(
                "final_energy_ratio_without_eroded"
            ),
            "final_hourglass_to_internal_ratio": metrics.get("final_hourglass_to_internal_ratio"),
            "warnings": _as_list(metrics.get("warnings")),
        },
    }

    impact_site = _as_dict(_as_dict(payload["torso_response"]).get("impact_site"))
    payload["injury_prediction_ready"] = (
        metrics.get("analysis_status") == "screening_metrics_computed"
        and _finite_number(initial_speed)
        and _finite_number(initial_ke)
        and _finite_number(projectile_mass)
        and _finite_number(projectile_mass_scale)
        and _finite_number(residual_speed)
        and _finite_number(metrics.get("projectile_residual_ke_j"))
        and _finite_number(kinetic_energy_loss)
        and _finite_number(impact_site.get("max_deflection_mm"))
        and _finite_number(impact_site.get("max_compression_ratio"))
        and _finite_number(impact_site.get("peak_vc_mps"))
    )
    return _validate_injury_prediction_input(payload)


def _write_case_outputs(root: Path, metadata: dict[str, object], result: dict[str, object]) -> None:
    injury_payload = build_injury_prediction_input(metadata, result)
    result["injury_prediction_input"] = injury_payload
    (root / INJURY_INPUT_FILENAME).write_text(
        json.dumps(injury_payload, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n"
    )
    (root / "metrics.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n"
    )


def analyze_case(case_dir: str | Path) -> dict[str, object]:
    root = Path(case_dir)
    metadata = json.loads((root / "case.json").read_text(encoding="utf-8"))
    metadata.setdefault("run_id", root.name)
    nodout_path = root / "nodout"
    if not nodout_path.is_file():
        initial_speed = float(metadata["case"]["speed_mps"])
        mass = float(metadata["projectile_mass_kg"])
        result = {
            "case_id": metadata["case_id"],
            "analysis_status": "missing_nodout",
            "screening_only": True,
            "armor_type": metadata["case"]["armor_type"],
            "caliber_mm": metadata["case"]["caliber_mm"],
            "impact_speed_mps": initial_speed,
            "yaw_deg": metadata["case"]["yaw_deg"],
            "pitch_deg": metadata["case"]["pitch_deg"],
            "impact_x_mm": metadata["case"].get("impact_x_mm"),
            "impact_z_mm": metadata["case"].get("impact_z_mm"),
            "mesh_scale": metadata["case"]["mesh_scale"],
            "projectile_mass_kg": mass,
            "projectile_initial_ke_j": 0.5 * mass * initial_speed ** 2,
            "body_depth_mm": metadata.get("body_depth_mm"),
            "simulation_duration_ms": None,
            "nodout_parse_status": "missing",
            "glstat_parse_status": (
                "not_parsed_without_nodout" if (root / "glstat").is_file() else "missing"
            ),
            "warnings": list(metadata.get("model_limitations", []))
            + ["NODOUT was not found; run the solver with ASCII output enabled."],
        }
        _write_case_outputs(root, metadata, result)
        return result

    frames = parse_nodout(nodout_path)
    sensors: dict[str, int] = {key: int(value) for key, value in metadata["sensors"].items()}
    depth_mm = float(metadata["body_depth_mm"])

    def region_metrics(region: str) -> tuple[dict[str, object], list[dict[str, float]]]:
        front_id = sensors[f"{region}_front"]
        back_id = sensors[f"{region}_back"]
        deletion_times = [
            value
            for value in (
                _node_deletion_time(root, front_id),
                _node_deletion_time(root, back_id),
            )
            if value is not None
        ]
        deletion_time = min(deletion_times) if deletion_times else None
        history_key = {
            "impact": "body_near_impact",
            "chest": "body_near_chest",
            "abdomen": "body_near_abdomen",
        }[region]
        history_element_id = _as_dict(metadata.get("history_elements")).get(history_key)
        element_failure_time = (
            _element_failure_time(root, history_element_id)
            if isinstance(history_element_id, int)
            else None
        )
        cutoff_times = [
            value for value in (deletion_time, element_failure_time) if value is not None
        ]
        cutoff_time = min(cutoff_times) if cutoff_times else None
        values, history = _pair_metrics(
            frames,
            front_id,
            back_id,
            depth_mm,
            max_time_ms=cutoff_time,
        )
        values.update({
            "front_node_id": front_id,
            "back_node_id": back_id,
            "measurement_validity": (
                "pre_tracked_entity_failure"
                if cutoff_time is not None
                else "full_parsed_nodout_node_and_tracked_element_history"
                if history and isinstance(history_element_id, int)
                else "full_parsed_nodout_node_history_only"
                if history
                else "not_available"
            ),
            "measurement_valid_through_ms": history[-1]["time_ms"] if history else None,
            "sensor_deletion_time_ms": deletion_time,
            "tracked_element_id": history_element_id,
            "tracked_element_failure_time_ms": element_failure_time,
            "tracked_element_monitoring_status": (
                "tracked" if isinstance(history_element_id, int) else "not_available_in_case_metadata"
            ),
        })
        return values, history

    local, local_history = region_metrics("impact")
    chest, _ = region_metrics("chest")
    abdomen, _ = region_metrics("abdomen")

    projectile_sensor = sensors["projectile_center"]
    projectile_deletion_time = _node_deletion_time(root, projectile_sensor)
    projectile_element_id = _as_dict(metadata.get("history_elements")).get("projectile")
    projectile_element_failure_time = (
        _element_failure_time(root, projectile_element_id)
        if isinstance(projectile_element_id, int)
        else None
    )
    residual_measurement = _residual_speed_metrics(
        frames,
        projectile_sensor,
        projectile_deletion_time,
        projectile_element_failure_time,
    )
    residual_value = residual_measurement.get("speed_mps")
    residual = float(residual_value) if _finite_number(residual_value) else None
    initial_speed = float(metadata["case"]["speed_mps"])
    mass = float(metadata["projectile_mass_kg"])
    initial_ke = 0.5 * mass * initial_speed ** 2
    residual_ke = 0.5 * mass * residual ** 2 if residual is not None else None
    kinetic_energy_loss = initial_ke - residual_ke if residual_ke is not None else None

    armor_sensor = sensors.get("armor_near_impact")
    armor_deletion_time = None
    armor_element_failure_time = None
    armor_local_failure_time = None
    armor_failure_basis: list[str] = []
    armor_times: list[float] = []
    armor_disp: list[float] = []
    if armor_sensor is not None:
        armor_deletion_time = _node_deletion_time(root, armor_sensor)
        armor_element_id = _as_dict(metadata.get("history_elements")).get("armor_near_impact")
        if isinstance(armor_element_id, int):
            armor_element_failure_time = _element_failure_time(root, armor_element_id)
        failure_times = [
            value
            for value in (armor_deletion_time, armor_element_failure_time)
            if value is not None
        ]
        armor_local_failure_time = min(failure_times) if failure_times else None
        if armor_deletion_time is not None:
            armor_failure_basis.append("armor_sensor_node_deletion_message")
        if armor_element_failure_time is not None:
            armor_failure_basis.append("near_impact_shell_element_failure_message")
        armor_times, armor_disp = _node_series(
            frames,
            armor_sensor,
            "displacement_mm",
            1,
            max_time_ms=armor_local_failure_time,
        )
    armor_peak = max((max(armor_disp), 0.0)) if armor_disp else None
    armor_peak_time = None
    if armor_peak is not None and armor_times:
        armor_peak_time = armor_times[armor_disp.index(armor_peak)]

    torso_acceleration_sensor = sensors["torso_center"]
    torso_acceleration_deletion_time = _node_deletion_time(root, torso_acceleration_sensor)
    acceleration = _acceleration_metrics(
        frames,
        torso_acceleration_sensor,
        max_time_ms=torso_acceleration_deletion_time,
    )

    glstat_path = root / "glstat"
    energy_frames = parse_glstat(glstat_path) if glstat_path.is_file() else []
    glstat_parse_status = (
        "parsed" if energy_frames else "parse_failed_or_empty" if glstat_path.is_file() else "missing"
    )
    final_energy = energy_frames[-1].values if energy_frames else {}
    energy_ratio = final_energy.get("energy_ratio")
    internal_energy = final_energy.get("internal_energy_j")
    hourglass_energy = final_energy.get("hourglass_energy_j")
    hourglass_fraction = None
    if internal_energy not in (None, 0.0) and hourglass_energy is not None:
        hourglass_fraction = abs(hourglass_energy / internal_energy)

    energy_sources = {
        "final_internal_energy_j": "internal_energy_j",
        "final_hourglass_energy_j": "hourglass_energy_j",
        "final_eroded_kinetic_energy_j": "eroded_kinetic_energy_j",
        "final_eroded_internal_energy_j": "eroded_internal_energy_j",
        "final_eroded_hourglass_energy_j": "eroded_hourglass_energy_j",
        "final_sliding_energy_j": "sliding_energy_j",
        "final_energy_ratio": "energy_ratio",
        "final_energy_ratio_without_eroded": "energy_ratio_without_eroded",
    }
    energy_field_status = {
        output_name: (
            "parsed"
            if source_name in final_energy
            else "not_available_in_final_glstat_frame"
            if energy_frames
            else glstat_parse_status
        )
        for output_name, source_name in energy_sources.items()
    }
    energy_field_status["final_hourglass_to_internal_ratio"] = (
        "computed"
        if hourglass_fraction is not None
        else "cannot_compute_internal_energy_zero"
        if internal_energy == 0.0 and hourglass_energy is not None
        else "not_available"
    )

    nodout_duration = frames[-1].time_ms if frames else None
    glstat_duration = energy_frames[-1].time_ms if energy_frames else None
    duration_candidates = [
        value for value in (nodout_duration, glstat_duration) if value is not None
    ]
    simulation_duration = max(duration_candidates) if duration_candidates else None

    warnings: list[str] = list(metadata.get("model_limitations", []))
    if not frames:
        warnings.append("NODOUT was present but no nodal frames could be parsed.")
    if energy_ratio is None:
        warnings.append("GLSTAT energy ratio was unavailable.")
    elif abs(energy_ratio - 1.0) > 0.05:
        warnings.append(f"Final energy ratio deviates from 1.0 by {abs(energy_ratio - 1.0):.1%}.")
    if hourglass_fraction is not None and hourglass_fraction > 0.10:
        warnings.append(f"Hourglass/internal energy ratio is high ({hourglass_fraction:.1%}).")
    if armor_local_failure_time is not None:
        warnings.append(
            f"Local armor failure was detected at {armor_local_failure_time:g} ms; "
            "armor displacement is limited to pre-failure history."
        )
    if projectile_deletion_time is not None:
        warnings.append(
            f"Projectile center node eroded at {projectile_deletion_time:g} ms; "
            "residual projectile response is unavailable."
        )
    if projectile_element_failure_time is not None:
        warnings.append(
            f"Tracked projectile element failed at {projectile_element_failure_time:g} ms; "
            "residual projectile response is unavailable."
        )
    if torso_acceleration_deletion_time is not None:
        warnings.append(
            f"Torso acceleration sensor node eroded at {torso_acceleration_deletion_time:g} ms; "
            "acceleration metrics are limited to pre-erosion history."
        )
    raw_acceleration = acceleration["peak_raw_g"]
    clip_acceleration = acceleration["peak_3ms_g"]

    result: dict[str, object] = {
        "case_id": metadata["case_id"],
        "analysis_status": "screening_metrics_computed",
        "screening_only": True,
        "armor_type": metadata["case"]["armor_type"],
        "caliber_mm": metadata["case"]["caliber_mm"],
        "impact_speed_mps": initial_speed,
        "yaw_deg": metadata["case"]["yaw_deg"],
        "pitch_deg": metadata["case"]["pitch_deg"],
        "impact_x_mm": metadata["case"].get("impact_x_mm"),
        "impact_z_mm": metadata["case"].get("impact_z_mm"),
        "mesh_scale": metadata["case"]["mesh_scale"],
        "projectile_mass_kg": mass,
        "projectile_initial_ke_j": initial_ke,
        "body_depth_mm": depth_mm,
        "projectile_residual_speed_mps": residual,
        "projectile_residual_ke_j": residual_ke,
        "projectile_kinetic_energy_loss_j": kinetic_energy_loss,
        "projectile_residual_measurement": residual_measurement,
        "armor_peak_ap_displacement_mm": armor_peak,
        "armor_peak_ap_displacement_time_ms": armor_peak_time,
        "armor_local_failure_detected": (
            armor_local_failure_time is not None if armor_sensor is not None else None
        ),
        "armor_local_failure_time_ms": armor_local_failure_time,
        "armor_local_failure_detection_basis": (
            armor_failure_basis if armor_failure_basis else ["no_failure_message_for_tracked_entities"]
        ) if armor_sensor is not None else ["not_applicable_no_armor_sensor"],
        "armor_sensor_deletion_time_ms": armor_deletion_time,
        "armor_displacement_history_scope": (
            "pre_local_failure" if armor_local_failure_time is not None else "full_parsed_nodout_history"
        ) if armor_sensor is not None else "not_applicable",
        "armor_displacement_measurement_end_time_ms": armor_times[-1] if armor_times else None,
        "impact_site": local,
        "chest": chest,
        "abdomen": abdomen,
        "torso_center_peak_acceleration_g": acceleration["preferred_peak_g"],
        "torso_center_peak_acceleration_raw_g": raw_acceleration,
        "torso_center_peak_acceleration_3ms_g": clip_acceleration,
        "torso_center_acceleration_preferred_metric": (
            "vector_average_3ms_peak_g" if clip_acceleration is not None else "raw_peak_g"
        ),
        "torso_acceleration_measurement_validity": (
            "pre_sensor_erosion"
            if torso_acceleration_deletion_time is not None
            else "full_parsed_nodout_history"
            if frames
            else "not_available"
        ),
        "torso_acceleration_measurement_end_time_ms": (
            min(simulation_duration, torso_acceleration_deletion_time)
            if simulation_duration is not None and torso_acceleration_deletion_time is not None
            else simulation_duration
        ),
        "torso_acceleration_sensor_deletion_time_ms": torso_acceleration_deletion_time,
        "simulation_duration_ms": simulation_duration,
        "nodout_parse_status": "parsed" if frames else "parse_failed_or_empty",
        "glstat_parse_status": glstat_parse_status,
        "energy_field_status": energy_field_status,
        "final_internal_energy_j": internal_energy,
        "final_hourglass_energy_j": hourglass_energy,
        "final_eroded_kinetic_energy_j": final_energy.get("eroded_kinetic_energy_j"),
        "final_eroded_internal_energy_j": final_energy.get("eroded_internal_energy_j"),
        "final_eroded_hourglass_energy_j": final_energy.get("eroded_hourglass_energy_j"),
        "final_sliding_energy_j": final_energy.get("sliding_energy_j"),
        "final_energy_ratio": energy_ratio,
        "final_energy_ratio_without_eroded": final_energy.get("energy_ratio_without_eroded"),
        "final_hourglass_to_internal_ratio": hourglass_fraction,
        "warnings": warnings,
    }
    _write_case_outputs(root, metadata, result)
    if local_history:
        with (root / "impact_history.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(local_history[0]))
            writer.writeheader()
            writer.writerows(local_history)
    return result


def analyze_study(study_dir: str | Path) -> Path:
    root = Path(study_dir)
    manifest_path = root / "manifest.csv"
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as handle:
        manifest = list(csv.DictReader(handle))
    results = [analyze_case(root / row["case_id"]) for row in manifest]
    summary_path = root / "summary.csv"
    injury_inputs_path = root / INJURY_INPUTS_FILENAME
    rows: list[dict[str, object]] = []
    with injury_inputs_path.open("w", encoding="utf-8", newline="\n") as injury_inputs:
        for result in results:
            impact = result.get("impact_site") if isinstance(result.get("impact_site"), dict) else {}
            chest = result.get("chest") if isinstance(result.get("chest"), dict) else {}
            abdomen = result.get("abdomen") if isinstance(result.get("abdomen"), dict) else {}
            injury_input = result.get("injury_prediction_input")
            if isinstance(injury_input, dict):
                injury_inputs.write(json.dumps(injury_input, ensure_ascii=False) + "\n")
            rows.append({
                "case_id": result.get("case_id"),
                "status": result.get("analysis_status"),
                "injury_prediction_ready": (
                    injury_input.get("injury_prediction_ready") if isinstance(injury_input, dict) else False
                ),
                "armor_type": result.get("armor_type"),
                "caliber_mm": result.get("caliber_mm"),
                "impact_speed_mps": result.get("impact_speed_mps"),
                "yaw_deg": result.get("yaw_deg"),
                "pitch_deg": result.get("pitch_deg"),
                "impact_x_mm": result.get("impact_x_mm"),
                "impact_z_mm": result.get("impact_z_mm"),
                "mesh_scale": result.get("mesh_scale"),
                "projectile_mass_kg": result.get("projectile_mass_kg"),
                "projectile_initial_ke_j": result.get("projectile_initial_ke_j"),
                "projectile_residual_ke_j": result.get("projectile_residual_ke_j"),
                "max_deflection_mm": impact.get("max_deflection_mm") if isinstance(impact, dict) else None,
                "max_compression_ratio": impact.get("max_compression_ratio") if isinstance(impact, dict) else None,
                "peak_vc_mps": impact.get("peak_vc_mps") if isinstance(impact, dict) else None,
                "chest_max_deflection_mm": chest.get("max_deflection_mm") if isinstance(chest, dict) else None,
                "chest_max_compression_ratio": (
                    chest.get("max_compression_ratio") if isinstance(chest, dict) else None
                ),
                "chest_peak_vc_mps": chest.get("peak_vc_mps") if isinstance(chest, dict) else None,
                "abdomen_max_deflection_mm": abdomen.get("max_deflection_mm") if isinstance(abdomen, dict) else None,
                "abdomen_max_compression_ratio": (
                    abdomen.get("max_compression_ratio") if isinstance(abdomen, dict) else None
                ),
                "abdomen_peak_vc_mps": abdomen.get("peak_vc_mps") if isinstance(abdomen, dict) else None,
                "torso_center_peak_acceleration_g": result.get("torso_center_peak_acceleration_g"),
                "torso_center_peak_acceleration_raw_g": result.get(
                    "torso_center_peak_acceleration_raw_g"
                ),
                "torso_center_peak_acceleration_3ms_g": result.get(
                    "torso_center_peak_acceleration_3ms_g"
                ),
                "armor_peak_ap_displacement_mm": result.get("armor_peak_ap_displacement_mm"),
                "armor_local_failure_detected": result.get("armor_local_failure_detected"),
                "armor_sensor_deletion_time_ms": result.get("armor_sensor_deletion_time_ms"),
                "projectile_residual_speed_mps": result.get("projectile_residual_speed_mps"),
                "projectile_kinetic_energy_loss_j": result.get(
                    "projectile_kinetic_energy_loss_j"
                ),
                "final_internal_energy_j": result.get("final_internal_energy_j"),
                "final_hourglass_energy_j": result.get("final_hourglass_energy_j"),
                "final_eroded_internal_energy_j": result.get("final_eroded_internal_energy_j"),
                "final_energy_ratio": result.get("final_energy_ratio"),
                "final_energy_ratio_without_eroded": result.get(
                    "final_energy_ratio_without_eroded"
                ),
                "final_hourglass_to_internal_ratio": result.get(
                    "final_hourglass_to_internal_ratio"
                ),
                "warnings": " | ".join(result.get("warnings", [])),
            })
    with summary_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["case_id"])
        writer.writeheader()
        writer.writerows(rows)
    return summary_path
