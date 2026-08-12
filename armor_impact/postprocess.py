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
INJURY_INPUT_SCHEMA_VERSION = "injury-prediction-input/v2"
INJURY_INPUT_FILENAME = "injury_prediction_input.json"
INJURY_INPUTS_FILENAME = "injury_prediction_inputs.jsonl"

INJURY_FEATURE_UNITS = {
    "body_depth_mm": "mm",
    "caliber_mm": "mm",
    "impact_speed_mps": "m/s",
    "yaw_deg": "deg",
    "pitch_deg": "deg",
    "impact_x_mm": "mm",
    "impact_z_mm": "mm",
    "projectile_mass_kg": "kg",
    "projectile_nominal_mass_kg": "kg",
    "projectile_mass_scale": "1",
    "projectile_effective_density_kg_m3": "kg/m^3",
    "projectile_initial_ke_j": "J",
    "projectile_residual_speed_mps": "m/s",
    "projectile_residual_ke_j": "J",
    "projectile_energy_change_j": "J",
    "projectile_energy_transfer_fraction": "1",
    "armor_peak_ap_displacement_mm": "mm",
    "armor_sensor_deletion_time_ms": "ms",
    "max_deflection_mm": "mm",
    "max_compression_ratio": "1",
    "peak_vc_mps": "m/s",
    "time_of_max_deflection_ms": "ms",
    "time_of_peak_vc_ms": "ms",
    "torso_center_peak_acceleration_g": "g",
    "torso_center_peak_acceleration_raw_g": "g",
    "torso_center_peak_acceleration_3ms_g": "g",
    "final_internal_energy_j": "J",
    "final_hourglass_energy_j": "J",
    "final_eroded_kinetic_energy_j": "J",
    "final_eroded_internal_energy_j": "J",
    "final_eroded_hourglass_energy_j": "J",
    "final_sliding_energy_j": "J",
    "final_energy_ratio": "1",
    "final_energy_ratio_without_eroded": "1",
    "final_hourglass_to_internal_ratio": "1",
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
    frames: Iterable[NodalFrame], front_id: int, back_id: int, component: int = 1
) -> tuple[list[float], list[float]]:
    times: list[float] = []
    values: list[float] = []
    for frame in frames:
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


def _acceleration_metrics(frames: Iterable[NodalFrame], node_id: int) -> dict[str, float | None]:
    states: list[tuple[float, NodeState]] = []
    for frame in frames:
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


def _residual_speed(frames: list[NodalFrame], node_id: int) -> float | None:
    speeds: list[float] = []
    for frame in frames:
        state = frame.nodes.get(node_id)
        if state is None:
            continue
        v = state.velocity_mps
        speeds.append(math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2))
    if not speeds:
        return None
    tail_size = max(1, len(speeds) // 10)
    return statistics.median(speeds[-tail_size:])


def _pair_metrics(
    frames: list[NodalFrame], front: int, back: int, depth_mm: float
) -> tuple[dict[str, float | None], list[dict[str, float]]]:
    times, compression = _aligned_difference(frames, front, back)
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
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _metric_triplet(metrics: dict[str, object], key: str) -> dict[str, object]:
    values = _as_dict(metrics.get(key))
    return {
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
    projectile_material["effective_density_kg_m3"] = projectile_effective_density
    projectile_material["mass_scale"] = projectile_mass_scale
    limitations = list(_as_list(metadata.get("model_limitations")))
    if _finite_number(projectile_mass_scale) and abs(float(projectile_mass_scale) - 1.0) > 0.01:
        density_warning = (
            "Requested projectile mass differs from the nominal spherical mass; "
            f"projectile density is scaled by {float(projectile_mass_scale):.6g}."
        )
        if density_warning not in limitations:
            limitations.append(density_warning)
    initial_ke = metrics.get("projectile_initial_ke_j")
    transferred = metrics.get("projectile_energy_change_j")
    transfer_fraction = None
    if _finite_number(initial_ke) and float(initial_ke) > 0 and _finite_number(transferred):
        transfer_fraction = float(transferred) / float(initial_ke)

    initial_speed = metrics.get("impact_speed_mps")
    residual_speed = metrics.get("projectile_residual_speed_mps")
    speed_loss = None
    speed_loss_fraction = None
    if _finite_number(initial_speed) and _finite_number(residual_speed):
        speed_loss = float(initial_speed) - float(residual_speed)
        if float(initial_speed) > 0:
            speed_loss_fraction = speed_loss / float(initial_speed)

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
            "surrogate_geometry": body_dimensions,
            "body_material": body_material,
            "armor_geometry": _as_dict(metadata.get("armor_geometry")),
            "armor_material": _as_dict(metadata.get("armor_material")),
            "projectile_material": projectile_material,
            "limitations": limitations,
        },
        "units": INJURY_FEATURE_UNITS,
        "impact_conditions": {
            "armor_type": metrics.get("armor_type", case.get("armor_type")),
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
            "projectile_energy_change_j": transferred,
            "projectile_energy_transfer_fraction": transfer_fraction,
            "projectile_speed_loss_mps": speed_loss,
            "projectile_speed_loss_fraction": speed_loss_fraction,
        },
        "armor_response": {
            "armor_peak_ap_displacement_mm": metrics.get("armor_peak_ap_displacement_mm"),
            "armor_local_failure_detected": metrics.get("armor_local_failure_detected"),
            "armor_sensor_deletion_time_ms": metrics.get("armor_sensor_deletion_time_ms"),
            "displacement_history_scope": metrics.get("armor_displacement_history_scope"),
        },
        "torso_response": {
            "body_depth_mm": metrics.get("body_depth_mm", metadata.get("body_depth_mm")),
            "impact_site": _metric_triplet(metrics, "impact_site"),
            "chest": _metric_triplet(metrics, "chest"),
            "abdomen": _metric_triplet(metrics, "abdomen"),
            "torso_center_peak_acceleration_g": metrics.get("torso_center_peak_acceleration_g"),
            "torso_center_peak_acceleration_raw_g": metrics.get(
                "torso_center_peak_acceleration_raw_g"
            ),
            "torso_center_peak_acceleration_3ms_g": metrics.get(
                "torso_center_peak_acceleration_3ms_g"
            ),
            "torso_center_acceleration_basis": "single_center_node",
            "torso_center_acceleration_preferred_metric": metrics.get(
                "torso_center_acceleration_preferred_metric"
            ),
        },
        "simulation_quality": {
            "analysis_status": metrics.get("analysis_status"),
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
        and _finite_number(impact_site.get("max_deflection_mm"))
        and _finite_number(impact_site.get("max_compression_ratio"))
        and _finite_number(impact_site.get("peak_vc_mps"))
    )
    return payload


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
            "warnings": ["NODOUT was not found; run the solver with ASCII output enabled."],
        }
        _write_case_outputs(root, metadata, result)
        return result

    frames = parse_nodout(nodout_path)
    sensors: dict[str, int] = {key: int(value) for key, value in metadata["sensors"].items()}
    depth_mm = float(metadata["body_depth_mm"])
    local, local_history = _pair_metrics(frames, sensors["impact_front"], sensors["impact_back"], depth_mm)
    chest, _ = _pair_metrics(frames, sensors["chest_front"], sensors["chest_back"], depth_mm)
    abdomen, _ = _pair_metrics(frames, sensors["abdomen_front"], sensors["abdomen_back"], depth_mm)

    residual = _residual_speed(frames, sensors["projectile_center"])
    initial_speed = float(metadata["case"]["speed_mps"])
    mass = float(metadata["projectile_mass_kg"])
    initial_ke = 0.5 * mass * initial_speed ** 2
    residual_ke = 0.5 * mass * residual ** 2 if residual is not None else None
    transferred = initial_ke - residual_ke if residual_ke is not None else None

    armor_sensor = sensors.get("armor_near_impact")
    armor_deletion_time = None
    armor_disp: list[float] = []
    if armor_sensor is not None:
        armor_deletion_time = _node_deletion_time(root, armor_sensor)
        _, armor_disp = _node_series(
            frames,
            armor_sensor,
            "displacement_mm",
            1,
            max_time_ms=armor_deletion_time,
        )
    armor_peak = max((max(armor_disp), 0.0)) if armor_disp else None

    acceleration = _acceleration_metrics(frames, sensors["torso_center"])

    energy_frames = parse_glstat(root / "glstat") if (root / "glstat").is_file() else []
    final_energy = energy_frames[-1].values if energy_frames else {}
    energy_ratio = final_energy.get("energy_ratio")
    internal_energy = final_energy.get("internal_energy_j")
    hourglass_energy = final_energy.get("hourglass_energy_j")
    hourglass_fraction = None
    if internal_energy not in (None, 0.0) and hourglass_energy is not None:
        hourglass_fraction = abs(hourglass_energy / internal_energy)

    warnings: list[str] = list(metadata.get("model_limitations", []))
    if not frames:
        warnings.append("NODOUT was present but no nodal frames could be parsed.")
    if energy_ratio is None:
        warnings.append("GLSTAT energy ratio was unavailable.")
    elif abs(energy_ratio - 1.0) > 0.05:
        warnings.append(f"Final energy ratio deviates from 1.0 by {abs(energy_ratio - 1.0):.1%}.")
    if hourglass_fraction is not None and hourglass_fraction > 0.10:
        warnings.append(f"Hourglass/internal energy ratio is high ({hourglass_fraction:.1%}).")
    if armor_deletion_time is not None:
        warnings.append(
            f"Armor sensor node eroded at {armor_deletion_time:g} ms; "
            "armor displacement is limited to pre-erosion history."
        )
    raw_acceleration = acceleration["peak_raw_g"]
    clip_acceleration = acceleration["peak_3ms_g"]
    if (
        raw_acceleration is not None
        and clip_acceleration is not None
        and raw_acceleration > 5.0 * max(clip_acceleration, 1.0e-12)
    ):
        warnings.append(
            "Raw torso-center nodal acceleration contains high-frequency spikes; "
            "the 3 ms vector-average value is preferred for screening."
        )

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
        "projectile_energy_change_j": transferred,
        "armor_peak_ap_displacement_mm": armor_peak,
        "armor_local_failure_detected": armor_deletion_time is not None,
        "armor_sensor_deletion_time_ms": armor_deletion_time,
        "armor_displacement_history_scope": (
            "pre_erosion" if armor_deletion_time is not None else "full_simulation"
        ) if armor_sensor is not None else "not_applicable",
        "impact_site": local,
        "chest": chest,
        "abdomen": abdomen,
        "torso_center_peak_acceleration_g": acceleration["preferred_peak_g"],
        "torso_center_peak_acceleration_raw_g": raw_acceleration,
        "torso_center_peak_acceleration_3ms_g": clip_acceleration,
        "torso_center_acceleration_preferred_metric": (
            "3_ms_vector_average" if clip_acceleration is not None else "raw_nodal_peak"
        ),
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
                "projectile_energy_change_j": result.get("projectile_energy_change_j"),
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
