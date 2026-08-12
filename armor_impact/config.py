from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from pathlib import Path
import math
import tomllib
from typing import Any, Iterable


class ConfigError(ValueError):
    """Raised when a study configuration is incomplete or inconsistent."""


@dataclass(frozen=True)
class SolverConfig:
    executable: str
    ncpus: int
    memory_mb: int
    timeout_minutes: float


@dataclass(frozen=True)
class MeshConfig:
    body_nx: int
    body_ny: int
    body_nz: int
    projectile_subdivisions: int


@dataclass(frozen=True)
class BodyConfig:
    width_mm: float
    depth_mm: float
    height_mm: float
    density_kg_m3: float
    bulk_modulus_mpa: float
    shear_short_mpa: float
    shear_long_mpa: float
    decay_per_ms: float


@dataclass(frozen=True)
class ArmorMaterial:
    name: str
    thickness_mm: float
    density_kg_m3: float
    youngs_modulus_gpa: float
    poisson: float
    yield_mpa: float
    tangent_modulus_mpa: float
    failure_strain: float


@dataclass(frozen=True)
class ArmorGeometry:
    gap_mm: float
    bulge_mm: float


@dataclass(frozen=True)
class ProjectileConfig:
    density_kg_m3: float
    youngs_modulus_gpa: float
    poisson: float
    standoff_mm: float


@dataclass(frozen=True)
class OutputConfig:
    history_dt_ms: float
    d3plot_dt_ms: float
    termination_ms: float


@dataclass(frozen=True)
class CaseSpec:
    index: int
    armor_type: str
    caliber_mm: float
    speed_mps: float
    yaw_deg: float
    pitch_deg: float
    impact_x_mm: float
    impact_z_mm: float
    mesh_scale: float
    projectile_mass_kg: float | None = None

    @property
    def case_id(self) -> str:
        def token(value: float) -> str:
            return f"{value:g}".replace("-", "m").replace(".", "p")

        case_id = (
            f"c{self.index:04d}_{self.armor_type}"
            f"_d{token(self.caliber_mm)}_v{token(self.speed_mps)}"
            f"_y{token(self.yaw_deg)}_p{token(self.pitch_deg)}"
            f"_m{token(self.mesh_scale)}"
        )
        if self.projectile_mass_kg is not None:
            case_id += f"_w{token(self.projectile_mass_kg)}"
        return case_id

    @property
    def direction(self) -> tuple[float, float, float]:
        """Velocity unit vector; yaw=pitch=0 points from the front along +Y."""
        yaw = math.radians(self.yaw_deg)
        pitch = math.radians(self.pitch_deg)
        return (
            math.sin(yaw) * math.cos(pitch),
            math.cos(yaw) * math.cos(pitch),
            math.sin(pitch),
        )


@dataclass(frozen=True)
class StudyConfig:
    solver: SolverConfig
    mesh: MeshConfig
    body: BodyConfig
    armor_geometry: ArmorGeometry
    armors: dict[str, ArmorMaterial]
    projectile: ProjectileConfig
    output: OutputConfig
    cases: tuple[CaseSpec, ...]


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    section = data.get(name)
    if not isinstance(section, dict):
        raise ConfigError(f"Missing TOML section [{name}]")
    return section


def _required(section: dict[str, Any], key: str, context: str) -> Any:
    if key not in section:
        raise ConfigError(f"Missing '{key}' in [{context}]")
    return section[key]


def _float_list(value: Any, label: str) -> list[float]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{label} must be a non-empty TOML array")
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{label} contains a non-numeric value") from exc


def _str_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(x, str) for x in value):
        raise ConfigError(f"{label} must be a non-empty string array")
    return list(value)


def _armor_material(name: str, raw: dict[str, Any]) -> ArmorMaterial:
    return ArmorMaterial(
        name=name,
        thickness_mm=float(_required(raw, "thickness_mm", f"armor.{name}")),
        density_kg_m3=float(_required(raw, "density_kg_m3", f"armor.{name}")),
        youngs_modulus_gpa=float(_required(raw, "youngs_modulus_gpa", f"armor.{name}")),
        poisson=float(_required(raw, "poisson", f"armor.{name}")),
        yield_mpa=float(_required(raw, "yield_mpa", f"armor.{name}")),
        tangent_modulus_mpa=float(_required(raw, "tangent_modulus_mpa", f"armor.{name}")),
        failure_strain=float(_required(raw, "failure_strain", f"armor.{name}")),
    )


def _positive(values: Iterable[tuple[str, float]]) -> None:
    for label, value in values:
        if value <= 0:
            raise ConfigError(f"{label} must be greater than zero (got {value})")


def load_config(path: str | Path) -> StudyConfig:
    source = Path(path)
    with source.open("rb") as handle:
        data = tomllib.load(handle)

    solver_raw = _section(data, "solver")
    solver = SolverConfig(
        executable=str(solver_raw.get("executable", "")),
        ncpus=int(solver_raw.get("ncpus", 2)),
        memory_mb=int(solver_raw.get("memory_mb", 2048)),
        timeout_minutes=float(solver_raw.get("timeout_minutes", 120)),
    )

    mesh_raw = _section(data, "mesh")
    mesh = MeshConfig(
        body_nx=int(mesh_raw.get("body_nx", 24)),
        body_ny=int(mesh_raw.get("body_ny", 8)),
        body_nz=int(mesh_raw.get("body_nz", 32)),
        projectile_subdivisions=int(mesh_raw.get("projectile_subdivisions", 2)),
    )

    body_raw = _section(data, "body")
    body = BodyConfig(
        width_mm=float(body_raw.get("width_mm", 360.0)),
        depth_mm=float(body_raw.get("depth_mm", 200.0)),
        height_mm=float(body_raw.get("height_mm", 500.0)),
        density_kg_m3=float(body_raw.get("density_kg_m3", 1000.0)),
        bulk_modulus_mpa=float(body_raw.get("bulk_modulus_mpa", 2200.0)),
        shear_short_mpa=float(body_raw.get("shear_short_mpa", 10.0)),
        shear_long_mpa=float(body_raw.get("shear_long_mpa", 3.0)),
        decay_per_ms=float(body_raw.get("decay_per_ms", 0.5)),
    )

    armor_raw = _section(data, "armor")
    geom_raw = armor_raw.get("geometry", {})
    if not isinstance(geom_raw, dict):
        raise ConfigError("[armor.geometry] must be a TOML table")
    armor_geometry = ArmorGeometry(
        gap_mm=float(geom_raw.get("gap_mm", 5.0)),
        bulge_mm=float(geom_raw.get("bulge_mm", 20.0)),
    )
    armors: dict[str, ArmorMaterial] = {}
    for name, value in armor_raw.items():
        if name == "geometry":
            continue
        if isinstance(value, dict):
            armors[name] = _armor_material(name, value)
    if not armors:
        raise ConfigError("At least one [armor.<name>] material table is required")

    projectile_raw = _section(data, "projectile")
    projectile = ProjectileConfig(
        density_kg_m3=float(projectile_raw.get("density_kg_m3", 7200.0)),
        youngs_modulus_gpa=float(projectile_raw.get("youngs_modulus_gpa", 120.0)),
        poisson=float(projectile_raw.get("poisson", 0.25)),
        standoff_mm=float(projectile_raw.get("standoff_mm", 40.0)),
    )

    output_raw = _section(data, "output")
    output = OutputConfig(
        history_dt_ms=float(output_raw.get("history_dt_ms", 0.01)),
        d3plot_dt_ms=float(output_raw.get("d3plot_dt_ms", 0.05)),
        termination_ms=float(output_raw.get("termination_ms", 5.0)),
    )

    study_raw = _section(data, "study")
    armor_types = _str_list(_required(study_raw, "armor_types", "study"), "study.armor_types")
    calibers = _float_list(_required(study_raw, "caliber_mm", "study"), "study.caliber_mm")
    speeds = _float_list(_required(study_raw, "speed_mps", "study"), "study.speed_mps")
    yaws = _float_list(study_raw.get("yaw_deg", [0.0]), "study.yaw_deg")
    pitches = _float_list(study_raw.get("pitch_deg", [0.0]), "study.pitch_deg")
    impact_x = _float_list(study_raw.get("impact_x_mm", [0.0]), "study.impact_x_mm")
    impact_z = _float_list(study_raw.get("impact_z_mm", [0.0]), "study.impact_z_mm")
    mesh_scales = _float_list(study_raw.get("mesh_scale", [1.0]), "study.mesh_scale")

    unknown = sorted(set(armor_types) - set(armors) - {"none"})
    if unknown:
        raise ConfigError(f"Unknown armor_types: {', '.join(unknown)}")

    combinations = product(armor_types, calibers, speeds, yaws, pitches, impact_x, impact_z, mesh_scales)
    cases = tuple(
        CaseSpec(index=i, armor_type=a, caliber_mm=d, speed_mps=v, yaw_deg=y,
                 pitch_deg=p, impact_x_mm=x, impact_z_mm=z, mesh_scale=m)
        for i, (a, d, v, y, p, x, z, m) in enumerate(combinations, start=1)
    )

    _positive([
        ("solver.ncpus", float(solver.ncpus)),
        ("solver.memory_mb", float(solver.memory_mb)),
        ("mesh.body_nx", float(mesh.body_nx)),
        ("mesh.body_ny", float(mesh.body_ny)),
        ("mesh.body_nz", float(mesh.body_nz)),
        ("body dimensions", min(body.width_mm, body.depth_mm, body.height_mm)),
        ("body.density_kg_m3", body.density_kg_m3),
        ("projectile.density_kg_m3", projectile.density_kg_m3),
        ("output.history_dt_ms", output.history_dt_ms),
        ("output.d3plot_dt_ms", output.d3plot_dt_ms),
        ("output.termination_ms", output.termination_ms),
    ])
    for case in cases:
        _positive([
            ("case caliber_mm", case.caliber_mm),
            ("case speed_mps", case.speed_mps),
            ("case mesh_scale", case.mesh_scale),
        ])
        if case.projectile_mass_kg is not None:
            _positive([("case projectile_mass_kg", case.projectile_mass_kg)])
        if abs(case.pitch_deg) >= 89.0:
            raise ConfigError("pitch_deg must stay between -89 and +89 degrees")
        if abs(case.impact_x_mm) >= body.width_mm / 2 or abs(case.impact_z_mm) >= body.height_mm / 2:
            raise ConfigError("Impact point must lie inside the torso front face")
    if not 0 <= mesh.projectile_subdivisions <= 4:
        raise ConfigError("projectile_subdivisions must be between 0 and 4")

    return StudyConfig(
        solver=solver,
        mesh=mesh,
        body=body,
        armor_geometry=armor_geometry,
        armors=armors,
        projectile=projectile,
        output=output,
        cases=cases,
    )
