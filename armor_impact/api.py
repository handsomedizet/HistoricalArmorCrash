"""Public library API for running one injury-screening simulation."""

from __future__ import annotations

from dataclasses import replace
import math
import os
from pathlib import Path
from typing import Any
import uuid

from .config import CaseSpec, load_config
from .deck import build_case
from .postprocess import analyze_case
from .runner import RunResult, run_case


ARMOR_TYPE_ALIASES = {
    "두정갑": "dujeong_equivalent",
    "두정": "dujeong_equivalent",
    "dujeong": "dujeong_equivalent",
    "dujeong_equivalent": "dujeong_equivalent",
    "플레이트": "plate",
    "판금갑옷": "plate",
    "plate": "plate",
    "plate_armor": "plate",
    "없음": "none",
    "무갑옷": "none",
    "none": "none",
    "no_armor": "none",
}
DEFAULT_CONFIG_PATH = Path(__file__).with_name("default_study.toml")
CONFIG_ENV_VAR = "ARMOR_IMPACT_CONFIG"


class InjuryPredictionError(RuntimeError):
    """Raised when LS-DYNA cannot produce prediction-ready response data."""

    def __init__(self, message: str, *, run_result: RunResult | None = None) -> None:
        super().__init__(message)
        self.run_result = run_result


def normalize_armor_type(armor_type: str) -> str:
    """Normalize Korean and English armor names used by the public API."""
    if not isinstance(armor_type, str) or not armor_type.strip():
        raise ValueError("armor_type must be one of: 두정갑, 플레이트, 없음")
    key = armor_type.strip().casefold().replace("-", "_").replace(" ", "_")
    try:
        return ARMOR_TYPE_ALIASES[key]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported armor_type {armor_type!r}; use 두정갑, 플레이트, or 없음"
        ) from exc


def _positive_number(name: str, value: object) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number greater than zero")
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number greater than zero") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be a finite number greater than zero")
    return number


def _resolve_config_path(config_path: str | Path | None) -> Path:
    if config_path is not None:
        return Path(config_path)

    configured = os.environ.get(CONFIG_ENV_VAR, "").strip()
    if configured:
        return Path(configured).expanduser()

    candidates = (
        Path.cwd() / "study.toml",
        Path(__file__).resolve().parents[1] / "study.toml",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return DEFAULT_CONFIG_PATH


def predict_injury(
    armor_type: str,
    speed_mps: float,
    caliber_mm: float,
    projectile_mass_kg: float,
    *,
    config_path: str | Path | None = None,
    output_dir: str | Path = "prediction_runs",
    yaw_deg: float = 0.0,
    pitch_deg: float = 0.0,
    impact_x_mm: float = 0.0,
    impact_z_mm: float = 0.0,
    mesh_scale: float = 1.0,
    simulation_duration_ms: float | None = None,
) -> dict[str, Any]:
    """Run one LS-DYNA case and return a JSON-serializable AI input dictionary.

    The four positional inputs use m/s, mm, and kg. The returned values are
    screening metrics from the homogeneous torso surrogate, not a clinical
    diagnosis or a validated human-body injury probability.
    """
    normalized_armor = normalize_armor_type(armor_type)
    speed = _positive_number("speed_mps", speed_mps)
    caliber = _positive_number("caliber_mm", caliber_mm)
    mass = _positive_number("projectile_mass_kg", projectile_mass_kg)
    scale = _positive_number("mesh_scale", mesh_scale)

    yaw = float(yaw_deg)
    pitch = float(pitch_deg)
    impact_x = float(impact_x_mm)
    impact_z = float(impact_z_mm)
    if not all(math.isfinite(value) for value in (yaw, pitch, impact_x, impact_z)):
        raise ValueError("Angles and impact coordinates must be finite numbers")
    if abs(pitch) >= 89.0:
        raise ValueError("pitch_deg must stay between -89 and +89 degrees")

    config_source = _resolve_config_path(config_path)
    config = load_config(config_source)
    if simulation_duration_ms is not None:
        duration = _positive_number("simulation_duration_ms", simulation_duration_ms)
        config = replace(
            config,
            output=replace(config.output, termination_ms=duration),
        )
    if normalized_armor != "none" and normalized_armor not in config.armors:
        raise ValueError(
            f"Armor material {normalized_armor!r} is missing from {config_source}"
        )
    if abs(impact_x) >= config.body.width_mm / 2 or abs(impact_z) >= config.body.height_mm / 2:
        raise ValueError("Impact point must lie inside the torso front face")

    case = CaseSpec(
        index=1,
        armor_type=normalized_armor,
        caliber_mm=caliber,
        speed_mps=speed,
        yaw_deg=yaw,
        pitch_deg=pitch,
        impact_x_mm=impact_x,
        impact_z_mm=impact_z,
        mesh_scale=scale,
        projectile_mass_kg=mass,
    )
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    case_dir = root / f"{case.case_id}_{uuid.uuid4().hex[:8]}"
    build_case(config, case, case_dir)

    run_result = run_case(case_dir, config.solver)
    if run_result.status != "completed":
        raise InjuryPredictionError(
            f"LS-DYNA case did not complete ({run_result.status}): {run_result.message}. "
            f"Case files: {case_dir.resolve()}",
            run_result=run_result,
        )

    analysis = analyze_case(case_dir)
    payload = analysis.get("injury_prediction_input")
    if not isinstance(payload, dict) or not payload.get("injury_prediction_ready"):
        raise InjuryPredictionError(
            f"Simulation completed but prediction-ready metrics were not produced. "
            f"Case files: {case_dir.resolve()}",
            run_result=run_result,
        )
    return payload
