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
TIME_EQ_RE = re.compile(rf"\btime\s*=\s*({FLOAT_PATTERN})", re.IGNORECASE)
INJURY_INPUT_SCHEMA_VERSION = "injury-prediction-input/v1"
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
    "projectile_initial_ke_j": "J",
    "projectile_residual_speed_mps": "m/s",
    "projectile_residual_ke_j": "J",
    "projectile_energy_change_j": "J",
    "projectile_energy_transfer_fraction": "1",
    "armor_peak_ap_displacement_mm": "mm",
    "max_deflection_mm": "mm",
    "max_compression_ratio": "1",
    "peak_vc_mps": "m/s",
    "torso_center_peak_acceleration_g": "g",
    "final_energy_ratio": "1",
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
            fields = line.split()
            if current_time is None or len(fields) < 13:
                continue
            try:
                node_id = int(fields[0])
                values = [parse_lsdyna_float(item) for item in fields[1:13]]
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
                if label not in lowered:
                    continue
                match = re.search(rf"{re.escape(label)}\s*=\s*({FLOAT_PATTERN})", lowered)
                if match:
                    values[ENERGY_LABELS[label]] = parse_lsdyna_float(match.group(1))
                break
    flush()
    return frames


def _node_series(frames: Iterable[NodalFrame], node_id: int, attribute: str, component: int) -> tuple[list[float], list[float]]:
    times: list[float] = []
    values: list[float] = []
    for frame in frames:
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


def _peak_acceleration_g(frames: Iterable[NodalFrame], node_id: int) -> float | None:
    peaks: list[float] = []
    for frame in frames:
        state = frame.nodes.get(node_id)
        if state is None:
            continue
        a = state.acceleration_mm_ms2
        magnitude_mm_ms2 = math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)
        peaks.append(magnitude_mm_ms2 * 1000.0 / 9.80665)
    return max(peaks) if peaks else None


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
    return {
        "max_deflection_mm": max(max(compression), 0.0),
        "max_compression_ratio": max(max(ratios), 0.0),
        "peak_vc_mps": max(vc),
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
    }


def build_injury_prediction_input(metadata: dict[str, object], metrics: dict[str, object]) -> dict[str, object]:
    """Return the compact, unit-labeled payload intended for downstream AI scoring."""
    case = _as_dict(metadata.get("case"))
    body_dimensions = _as_dict(metadata.get("body_dimensions_mm"))
    if not body_dimensions and metadata.get("body_depth_mm") is not None:
        body_dimensions = {"depth_mm": metadata.get("body_depth_mm")}
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
        "prediction_task": "thoracoabdominal_injury_risk_screening",
        "model_context": {
            "model_type": "homogeneous_viscoelastic_torso_surrogate",
            "screening_only": bool(metrics.get("screening_only", True)),
            "surrogate_geometry": body_dimensions,
            "body_material": _as_dict(metadata.get("body_material")),
            "armor_geometry": _as_dict(metadata.get("armor_geometry")),
            "armor_material": _as_dict(metadata.get("armor_material")),
            "projectile_material": _as_dict(metadata.get("projectile_material")),
            "limitations": _as_list(metadata.get("model_limitations")),
        },
        "units": INJURY_FEATURE_UNITS,
        "impact_conditions": {
            "armor_type": metrics.get("armor_type", case.get("armor_type")),
            "caliber_mm": metrics.get("caliber_mm", case.get("caliber_mm")),
            "impact_speed_mps": initial_speed,
            "yaw_deg": metrics.get("yaw_deg", case.get("yaw_deg")),
            "pitch_deg": metrics.get("pitch_deg", case.get("pitch_deg")),
            "impact_x_mm": metrics.get("impact_x_mm", case.get("impact_x_mm")),
            "impact_z_mm": metrics.get("impact_z_mm", case.get("impact_z_mm")),
            "mesh_scale": metrics.get("mesh_scale", case.get("mesh_scale")),
            "projectile_mass_kg": metrics.get("projectile_mass_kg"),
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
        },
        "torso_response": {
            "body_depth_mm": metrics.get("body_depth_mm", metadata.get("body_depth_mm")),
            "impact_site": _metric_triplet(metrics, "impact_site"),
            "chest": _metric_triplet(metrics, "chest"),
            "abdomen": _metric_triplet(metrics, "abdomen"),
            "torso_center_peak_acceleration_g": metrics.get("torso_center_peak_acceleration_g"),
        },
        "simulation_quality": {
            "analysis_status": metrics.get("analysis_status"),
            "final_energy_ratio": metrics.get("final_energy_ratio"),
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

    armor_times, armor_disp = _node_series(frames, sensors["armor_near_impact"], "displacement_mm", 1)
    armor_peak = max((max(armor_disp), 0.0)) if armor_disp else None

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
        "impact_site": local,
        "chest": chest,
        "abdomen": abdomen,
        "torso_center_peak_acceleration_g": _peak_acceleration_g(frames, sensors["torso_center"]),
        "final_energy_ratio": energy_ratio,
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
                "projectile_residual_speed_mps": result.get("projectile_residual_speed_mps"),
                "projectile_energy_change_j": result.get("projectile_energy_change_j"),
                "final_energy_ratio": result.get("final_energy_ratio"),
                "warnings": " | ".join(result.get("warnings", [])),
            })
    with summary_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["case_id"])
        writer.writeheader()
        writer.writerows(rows)
    return summary_path
