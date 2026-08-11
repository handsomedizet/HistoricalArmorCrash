from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import csv
import json
import math

from .config import CaseSpec, StudyConfig
from .mesh import MeshModel, build_mesh, iter_projectile_node_ids


def _fmt(value: float | int) -> str:
    if isinstance(value, int):
        return str(value)
    # LS-DYNA still applies a 10-character limit to individual values in many
    # comma-delimited keyword cards. Use as much precision as fits instead of
    # emitting long values such as ``7.45217606e-06`` (KEY+459).
    for precision in range(9, 0, -1):
        text = f"{value:.{precision}g}"
        if len(text) <= 10:
            return text
    raise ValueError(f"Value cannot be represented in a 10-character LS-DYNA field: {value!r}")


def _line(*values: float | int | str) -> str:
    return ",".join(_fmt(v) if isinstance(v, (float, int)) else v for v in values)


def _armor_surface_y(config: StudyConfig, x_mm: float, z_mm: float) -> float:
    body = config.body
    geom = config.armor_geometry
    u = max(0.0, min(1.0, (x_mm + body.width_mm / 2) / body.width_mm))
    w = max(0.0, min(1.0, (z_mm + body.height_mm / 2) / body.height_mm))
    shape = math.sin(math.pi * u) * math.sin(math.pi * w)
    return -body.depth_mm / 2 - geom.gap_mm - geom.bulge_mm * shape


def _projectile_center(config: StudyConfig, case: CaseSpec) -> tuple[float, float, float]:
    direction = case.direction
    radius = case.caliber_mm / 2
    target = (
        case.impact_x_mm,
        _armor_surface_y(config, case.impact_x_mm, case.impact_z_mm),
        case.impact_z_mm,
    )
    offset = radius + config.projectile.standoff_mm
    return tuple(target[i] - direction[i] * offset for i in range(3))  # type: ignore[return-value]


def _projectile_mass_kg(config: StudyConfig, case: CaseSpec) -> float:
    radius_m = case.caliber_mm / 2000.0
    return config.projectile.density_kg_m3 * (4.0 / 3.0) * math.pi * radius_m ** 3


def build_case(config: StudyConfig, case: CaseSpec, case_dir: Path) -> dict[str, object]:
    case_dir.mkdir(parents=True, exist_ok=True)
    center = _projectile_center(config, case)
    nx = max(2, round(config.mesh.body_nx * case.mesh_scale))
    ny = max(2, round(config.mesh.body_ny * case.mesh_scale))
    nz = max(2, round(config.mesh.body_nz * case.mesh_scale))
    mesh = build_mesh(
        width_mm=config.body.width_mm,
        depth_mm=config.body.depth_mm,
        height_mm=config.body.height_mm,
        nx=nx,
        ny=ny,
        nz=nz,
        armor_gap_mm=config.armor_geometry.gap_mm,
        armor_bulge_mm=config.armor_geometry.bulge_mm,
        projectile_center=center,
        projectile_radius_mm=case.caliber_mm / 2,
        projectile_subdivisions=config.mesh.projectile_subdivisions,
        impact_x_mm=case.impact_x_mm,
        impact_z_mm=case.impact_z_mm,
    )
    projectile_mass = _projectile_mass_kg(config, case)
    deck = render_deck(config, case, mesh, projectile_mass)
    (case_dir / "run.k").write_text(deck, encoding="utf-8", newline="\n")

    metadata: dict[str, object] = {
        "case": asdict(case),
        "case_id": case.case_id,
        "direction": list(case.direction),
        "projectile_center_mm": list(center),
        "projectile_mass_kg": projectile_mass,
        "projectile_mesh_volume_mm3": mesh.projectile_mesh_volume_mm3,
        "body_depth_mm": config.body.depth_mm,
        "body_dimensions_mm": {
            "width_mm": config.body.width_mm,
            "depth_mm": config.body.depth_mm,
            "height_mm": config.body.height_mm,
        },
        "body_material": asdict(config.body),
        "armor_geometry": asdict(config.armor_geometry),
        "armor_material": asdict(config.armors[case.armor_type]),
        "projectile_material": asdict(config.projectile),
        "mesh_divisions": {"nx": nx, "ny": ny, "nz": nz, "scale": case.mesh_scale},
        "sensors": mesh.sensors,
        "history_elements": mesh.history_elements,
        "model_limitations": [
            "Homogeneous viscoelastic torso surrogate; not a validated human body model.",
            "Dujeong armor is a homogenized equivalent panel, not discrete plates and textile.",
            "Material values are illustrative defaults and require calibration.",
        ],
    }
    (case_dir / "case.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n"
    )
    return metadata


def build_study(config: StudyConfig, output_dir: str | Path) -> Path:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for case in config.cases:
        metadata = build_case(config, case, root / case.case_id)
        rows.append({
            "case_id": case.case_id,
            "armor_type": case.armor_type,
            "caliber_mm": case.caliber_mm,
            "speed_mps": case.speed_mps,
            "yaw_deg": case.yaw_deg,
            "pitch_deg": case.pitch_deg,
            "impact_x_mm": case.impact_x_mm,
            "impact_z_mm": case.impact_z_mm,
            "mesh_scale": case.mesh_scale,
            "projectile_mass_kg": metadata["projectile_mass_kg"],
            "status": "built",
        })
    manifest = root / "manifest.csv"
    with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["case_id"])
        writer.writeheader()
        writer.writerows(rows)
    return manifest


def render_deck(config: StudyConfig, case: CaseSpec, mesh: MeshModel, projectile_mass_kg: float) -> str:
    armor = config.armors[case.armor_type]
    direction = case.direction
    velocity = tuple(component * case.speed_mps for component in direction)

    physical_density = config.projectile.density_kg_m3 / 1.0e9
    analytic_volume = projectile_mass_kg / physical_density
    effective_projectile_density = projectile_mass_kg / mesh.projectile_mesh_volume_mm3
    density_scale = effective_projectile_density / physical_density

    out: list[str] = [
        "*KEYWORD",
        "*TITLE",
        f"Armor impact screening - {case.case_id}",
        "$ Units: mm, ms, kg, GPa, kN, J. Velocity numeric value equals m/s.",
        "$ This is a screening surrogate, not a validated injury-risk model.",
        "*CONTROL_TERMINATION",
        _line(config.output.termination_ms),
        "*CONTROL_ENERGY",
        _line(2, 2, 2, 2, 2, 2, 1, 1),
        "*DATABASE_BINARY_D3PLOT",
        _line(config.output.d3plot_dt_ms),
        "*DATABASE_GLSTAT",
        _line(config.output.history_dt_ms, 1),
        "*DATABASE_MATSUM",
        _line(config.output.history_dt_ms, 1),
        "*DATABASE_NODOUT",
        _line(config.output.history_dt_ms, 1),
        "*DATABASE_ELOUT",
        _line(config.output.history_dt_ms, 1),
        "*DATABASE_RCFORC",
        _line(config.output.history_dt_ms, 1),
        "*DATABASE_SLEOUT",
        _line(config.output.history_dt_ms, 1),
        "*DATABASE_HISTORY_NODE",
    ]
    sensor_ids = list(dict.fromkeys(mesh.sensors.values()))
    for start in range(0, len(sensor_ids), 8):
        out.append(_line(*sensor_ids[start:start + 8]))
    out.extend([
        "*DATABASE_HISTORY_SOLID",
        _line(mesh.history_elements["body_near_impact"], mesh.history_elements["projectile"]),
        "*DATABASE_HISTORY_SHELL",
        _line(mesh.history_elements["armor_near_impact"]),
        "*SECTION_SOLID",
        _line(1, 2),
        "*SECTION_SHELL",
        _line(2, 16, 0.833333, 5, 0, 0, 0, 1),
        _line(armor.thickness_mm, armor.thickness_mm, armor.thickness_mm, armor.thickness_mm),
        "*SECTION_SOLID",
        _line(3, 10),
        "*MAT_VISCOELASTIC",
        _line(
            1,
            config.body.density_kg_m3 / 1.0e9,
            config.body.bulk_modulus_mpa / 1000.0,
            config.body.shear_short_mpa / 1000.0,
            config.body.shear_long_mpa / 1000.0,
            config.body.decay_per_ms,
        ),
        "*MAT_PLASTIC_KINEMATIC",
        _line(
            2,
            armor.density_kg_m3 / 1.0e9,
            armor.youngs_modulus_gpa,
            armor.poisson,
            armor.yield_mpa / 1000.0,
            armor.tangent_modulus_mpa / 1000.0,
            1.0,
        ),
        _line(0.0, 0.0, armor.failure_strain, 0.0),
        "*MAT_ELASTIC",
        _line(
            3,
            effective_projectile_density,
            config.projectile.youngs_modulus_gpa,
            config.projectile.poisson,
        ),
        "$ Projectile density is volume-corrected for the faceted sphere mesh.",
        f"$ Analytic volume={analytic_volume:.9g} mm^3; density scale={density_scale:.9g}",
        "*PART",
        "Torso surrogate - unvalidated",
        _line(1, 1, 1, 0, 0, 0, 0, 0),
        "*PART",
        f"Armor - {case.armor_type}",
        _line(2, 2, 2, 0, 0, 0, 0, 0),
        "*PART",
        "Cast-iron cannonball approximation",
        _line(3, 3, 3, 0, 0, 0, 0, 0),
        "*NODE",
    ])
    for nid, (x, y, z) in sorted(mesh.nodes.items()):
        out.append(_line(nid, x, y, z))

    out.append("*ELEMENT_SOLID")
    for eid, pid, conn in mesh.body_elements:
        out.append(_line(eid, pid))
        out.append(_line(*conn, 0, 0))
    for eid, pid, conn in mesh.projectile_elements:
        out.append(_line(eid, pid))
        out.append(_line(*conn))

    out.append("*ELEMENT_SHELL")
    for eid, pid, conn in mesh.armor_elements:
        out.append(_line(eid, pid, *conn))

    out.extend([
        "*CONTACT_ERODING_SINGLE_SURFACE",
        _line(0, 0, 0, 0, 0, 0, 0, 0),
        _line(0.15, 0.10, 0.0, 0.0, 10.0, 0, 0.0, 1.0e20),
        _line(1.0, 1.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0),
        _line(0, 1, 1),
        "*INITIAL_VELOCITY_NODE",
    ])
    for nid in iter_projectile_node_ids(mesh):
        out.append(_line(nid, velocity[0], velocity[1], velocity[2], 0.0, 0.0, 0.0, 0))
    out.extend(["*END", ""])
    return "\n".join(out)
