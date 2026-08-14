from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import csv
import json
import math

from .config import CaseSpec, ShellMaterial, StudyConfig
from .mesh import MeshModel, build_mesh, iter_projectile_node_ids


CONTACT_PART_SET_ID = 900_001


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


def _torso_surface_y(config: StudyConfig, x_mm: float, z_mm: float) -> float:
    del z_mm
    semi_width = config.body.width_mm / 2.0
    semi_depth = config.body.depth_mm / 2.0
    ratio = max(-1.0, min(1.0, x_mm / semi_width))
    return -semi_depth * math.sqrt(max(0.0, 1.0 - ratio * ratio))


def _armor_outer_surface_y(
    config: StudyConfig, case: CaseSpec, x_mm: float, z_mm: float
) -> float:
    armor = config.armors[case.armor_type]
    torso_y = _torso_surface_y(config, x_mm, z_mm)
    semi_width = config.body.width_mm / 2.0
    semi_depth = config.body.depth_mm / 2.0
    normal_x = x_mm / (semi_width * semi_width)
    normal_y = torso_y / (semi_depth * semi_depth)
    normal_length = math.hypot(normal_x, normal_y)
    outward_y = normal_y / normal_length
    impact_thickness = (
        armor.reinforcement.thickness_mm
        if armor.construction == "riveted_discrete_plates"
        and armor.reinforcement is not None
        else armor.thickness_mm
    )
    surface_offset = (
        config.armor_geometry.gap_mm
        + armor.reference_thickness_mm / 2.0
        + impact_thickness / 2.0
    )
    return (
        torso_y
        + outward_y * surface_offset
    )


def _projectile_center(config: StudyConfig, case: CaseSpec) -> tuple[float, float, float]:
    direction = case.direction
    radius = case.caliber_mm / 2
    target_y = _torso_surface_y(config, case.impact_x_mm, case.impact_z_mm)
    if case.armor_type != "none":
        target_y = _armor_outer_surface_y(
            config, case, case.impact_x_mm, case.impact_z_mm
        )
    target = (
        case.impact_x_mm,
        target_y,
        case.impact_z_mm,
    )
    offset = radius + config.projectile.standoff_mm
    return tuple(target[i] - direction[i] * offset for i in range(3))  # type: ignore[return-value]


def _projectile_mass_kg(config: StudyConfig, case: CaseSpec) -> float:
    radius_m = case.caliber_mm / 2000.0
    return config.projectile.density_kg_m3 * (4.0 / 3.0) * math.pi * radius_m ** 3


def _armor_torso_geometry_diagnostics(mesh: MeshModel) -> dict[str, object]:
    """Measure the generated shell-to-solid clearance from actual node coordinates."""
    torso_by_armor = dict(mesh.armor_torso_node_pairs)
    clearances: list[float] = []
    outward_alignment: list[float] = []
    for _, pid, armor_conn in mesh.armor_elements:
        armor_thickness_mm = mesh.armor_part_thickness_mm[pid]
        a, b, c = (mesh.nodes[nid] for nid in armor_conn[:3])
        ab = tuple(b[i] - a[i] for i in range(3))
        ac = tuple(c[i] - a[i] for i in range(3))
        cross = (
            ab[1] * ac[2] - ab[2] * ac[1],
            ab[2] * ac[0] - ab[0] * ac[2],
            ab[0] * ac[1] - ab[1] * ac[0],
        )
        length = math.sqrt(sum(value * value for value in cross))
        if length <= 0:
            raise RuntimeError("Degenerate armor shell element generated")
        normal = tuple(value / length for value in cross)
        separations: list[float] = []
        alignments: list[float] = []
        for armor_nid in armor_conn:
            body_nid = torso_by_armor.get(armor_nid)
            if body_nid is None:
                raise RuntimeError("Armor node has no paired torso-surface node")
            armor_xyz = mesh.nodes[armor_nid]
            body_xyz = mesh.nodes[body_nid]
            delta = tuple(armor_xyz[i] - body_xyz[i] for i in range(3))
            separations.append(abs(sum(delta[i] * normal[i] for i in range(3))))
            delta_length = math.sqrt(sum(value * value for value in delta))
            alignments.append(sum(delta[i] * normal[i] for i in range(3)) / delta_length)
        clearances.append(sum(separations) / len(separations) - armor_thickness_mm / 2.0)
        outward_alignment.append(sum(alignments) / len(alignments))

    if not clearances:
        raise RuntimeError("Armor is enabled but no armor shell geometry was generated")
    if min(outward_alignment) <= 0:
        raise RuntimeError("Armor shell normals do not consistently point away from the torso")

    min_clearance = min(clearances)
    max_clearance = max(clearances)
    if min_clearance < -1.0e-6:
        raise RuntimeError(
            "Armor-torso initial penetration detected from generated node geometry "
            f"(minimum surface clearance {min_clearance:.6g} mm)."
        )

    armor_impact_nid = mesh.sensors["armor_near_impact"]
    torso_impact_nid = torso_by_armor[armor_impact_nid]
    armor_impact = mesh.nodes[armor_impact_nid]
    torso_impact = mesh.nodes[torso_impact_nid]
    impact_eid = mesh.history_elements["armor_near_impact"]
    impact_pid = next(pid for eid, pid, _ in mesh.armor_elements if eid == impact_eid)
    armor_thickness_mm = mesh.armor_part_thickness_mm[impact_pid]
    separation = math.dist(armor_impact, torso_impact)
    direction = tuple(
        (armor_impact[i] - torso_impact[i]) / separation for i in range(3)
    )
    torso_facing_surface = tuple(
        armor_impact[i] - direction[i] * armor_thickness_mm / 2.0 for i in range(3)
    )
    outer_surface = tuple(
        armor_impact[i] + direction[i] * armor_thickness_mm / 2.0 for i in range(3)
    )
    return {
        "measurement_basis": "paired generated shell/solid nodes and corresponding element-plane normals",
        "torso_anterior_node_at_impact_mm": list(torso_impact),
        "armor_mid_surface_node_at_impact_mm": list(armor_impact),
        "armor_torso_facing_surface_at_impact_mm": list(torso_facing_surface),
        "armor_outer_surface_at_impact_mm": list(outer_surface),
        "armor_torso_facing_surface_y_at_impact_mm": torso_facing_surface[1],
        "armor_outer_surface_y_at_impact_mm": outer_surface[1],
        "midplane_separation_at_impact_mm": separation,
        "surface_clearance_at_impact_mm": separation - armor_thickness_mm / 2.0,
        "surface_clearance_range_mm": {
            "minimum": min_clearance,
            "maximum": max_clearance,
        },
        "armor_shell_thickness_at_impact_mm": armor_thickness_mm,
        "armor_part_id_at_impact": impact_pid,
        "armor_part_role_at_impact": mesh.armor_part_roles[impact_pid],
        "armor_part_thickness_mm": {
            str(pid): thickness for pid, thickness in mesh.armor_part_thickness_mm.items()
        },
        "shell_reference_surface": "mid_surface_no_section_offset",
        "initial_penetration_status": "clear",
        "armor_shell_normal_status": "outward_away_from_torso",
        "body_cross_section": "elliptical_structured_solid",
    }


def _armor_construction_diagnostics(
    mesh: MeshModel, armor_material
) -> dict[str, object]:
    total_area = sum(mesh.armor_area_by_part_mm2.values())
    areas = {
        str(pid): area for pid, area in sorted(mesh.armor_area_by_part_mm2.items())
    }
    masses: dict[str, float] = {}
    total_mass = 0.0
    for pid, area in mesh.armor_area_by_part_mm2.items():
        material: ShellMaterial
        if pid == 4:
            if armor_material.reinforcement is None:
                raise RuntimeError("Reinforcement part exists without reinforcement material")
            material = armor_material.reinforcement
        else:
            material = armor_material
        mass = area * material.thickness_mm * material.density_kg_m3 / 1.0e9
        masses[str(pid)] = mass
        total_mass += mass
    reinforcement_area = mesh.armor_area_by_part_mm2.get(4, 0.0)
    return {
        "construction": armor_material.construction,
        "wrap_mode": armor_material.wrap_mode,
        "part_roles": {str(pid): role for pid, role in mesh.armor_part_roles.items()},
        "area_by_part_mm2": areas,
        "mass_by_part_kg": masses,
        "total_shell_area_mm2": total_area,
        "estimated_shell_mass_kg": total_mass,
        "mean_areal_density_kg_m2": total_mass / (total_area * 1.0e-6),
        "reinforcement_area_fraction": (
            reinforcement_area / total_area if total_area > 0 else 0.0
        ),
        "plate_pattern": (
            {
                "plate_width_mm": armor_material.plate_width_mm,
                "plate_height_mm": armor_material.plate_height_mm,
                "plate_gap_mm": armor_material.plate_gap_mm,
                "representation": "mesh-resolved iron sections joined by textile sections",
            }
            if armor_material.reinforcement is not None
            else None
        ),
    }


def _validate_generated_armor_contact(deck: str, armor_part_ids: list[int]) -> None:
    expected_set = "\n".join([
        "*SET_PART_LIST",
        str(CONTACT_PART_SET_ID),
        ",".join(str(pid) for pid in [1, *armor_part_ids, 3]),
    ])
    expected_contact = "\n".join([
        "*CONTACT_ERODING_SINGLE_SURFACE",
        _line(CONTACT_PART_SET_ID, 0, 2, 0, 0, 0, 0, 0),
    ])
    if expected_set not in deck or expected_contact not in deck:
        raise RuntimeError(
            "Armor is enabled but no valid armor-torso contact definition was generated."
        )


def build_case(config: StudyConfig, case: CaseSpec, case_dir: Path) -> dict[str, object]:
    case_dir.mkdir(parents=True, exist_ok=True)
    has_armor = case.armor_type != "none"
    if has_armor and case.armor_type not in config.armors:
        raise ValueError(f"Unknown armor type: {case.armor_type}")
    armor = config.armors.get(case.armor_type)
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
        armor_shell_thickness_mm=armor.thickness_mm if armor is not None else 0.0,
        armor_construction=armor.construction if armor is not None else "continuous_plate",
        armor_wrap_mode=armor.wrap_mode if armor is not None else "front_half",
        reinforcement_thickness_mm=(
            armor.reinforcement.thickness_mm
            if armor is not None and armor.reinforcement is not None
            else None
        ),
        plate_width_mm=armor.plate_width_mm if armor is not None else None,
        plate_height_mm=armor.plate_height_mm if armor is not None else None,
        plate_gap_mm=armor.plate_gap_mm if armor is not None else None,
        projectile_center=center,
        projectile_radius_mm=case.caliber_mm / 2,
        projectile_subdivisions=config.mesh.projectile_subdivisions,
        impact_x_mm=case.impact_x_mm,
        impact_z_mm=case.impact_z_mm,
        include_armor=has_armor,
    )
    nominal_projectile_mass = _projectile_mass_kg(config, case)
    projectile_mass = case.projectile_mass_kg
    if projectile_mass is None:
        projectile_mass = nominal_projectile_mass
    if projectile_mass <= 0:
        raise ValueError("projectile_mass_kg must be greater than zero")
    projectile_mass_scale = projectile_mass / nominal_projectile_mass
    armor_geometry_diagnostics = (
        _armor_torso_geometry_diagnostics(mesh)
        if armor is not None
        else {}
    )
    armor_construction_diagnostics = (
        _armor_construction_diagnostics(mesh, armor) if armor is not None else {}
    )
    deck = render_deck(config, case, mesh, projectile_mass)
    if has_armor:
        _validate_generated_armor_contact(deck, sorted(mesh.armor_part_thickness_mm))
    (case_dir / "run.k").write_text(deck, encoding="utf-8", newline="\n")

    metadata: dict[str, object] = {
        "case": asdict(case),
        "case_id": case.case_id,
        "run_id": case_dir.name,
        "direction": list(case.direction),
        "projectile_center_mm": list(center),
        "projectile_mass_kg": projectile_mass,
        "projectile_nominal_mass_kg": nominal_projectile_mass,
        "projectile_mass_scale": projectile_mass_scale,
        "projectile_effective_density_kg_m3": (
            config.projectile.density_kg_m3 * projectile_mass_scale
        ),
        "projectile_mesh_volume_mm3": mesh.projectile_mesh_volume_mm3,
        "body_depth_mm": config.body.depth_mm,
        "body_dimensions_mm": {
            "width_mm": config.body.width_mm,
            "depth_mm": config.body.depth_mm,
            "height_mm": config.body.height_mm,
        },
        "body_material": asdict(config.body),
        "armor_geometry": asdict(config.armor_geometry) if has_armor else {},
        "armor_torso_geometry": armor_geometry_diagnostics,
        "armor_construction": armor_construction_diagnostics,
        "armor_material": asdict(config.armors[case.armor_type]) if has_armor else None,
        "projectile_material": asdict(config.projectile),
        "simulation_duration_ms": config.output.termination_ms,
        "output_controls": asdict(config.output),
        "armor_torso_contact_status": "configured" if has_armor else "not_applicable",
        "armor_torso_contact": (
            {
                "status": "configured",
                "keyword": "*CONTACT_ERODING_SINGLE_SURFACE",
                "part_set_id": CONTACT_PART_SET_ID,
                "part_ids": [1, *sorted(mesh.armor_part_thickness_mm), 3],
                "torso_part_id": 1,
                "armor_part_ids": sorted(mesh.armor_part_thickness_mm),
                "projectile_part_id": 3,
                "surface_selection": "explicit_part_set",
                "shell_contact_thickness": "actual_section_thickness_via_CONTROL_CONTACT_SSTHK_1",
            }
            if has_armor
            else {
                "status": "not_applicable",
                "part_set_id": CONTACT_PART_SET_ID,
                "part_ids": [1, 3],
            }
        ),
        "mesh_divisions": {"nx": nx, "ny": ny, "nz": nz, "scale": case.mesh_scale},
        "sensors": mesh.sensors,
        "history_elements": mesh.history_elements,
        "model_limitations": [
            "Homogeneous viscoelastic torso surrogate; not a validated human body model.",
            "Material values are illustrative defaults and require calibration.",
            "Torso acceleration is measured at a single center node, not the torso center of mass.",
        ] + ([
            "Dujeong iron/textile sections are mesh-resolved, but rivets, plate overlap, "
            "fabric orthotropy, and layer separation still require coupon-level calibration."
        ] if case.armor_type == "dujeong_equivalent" else []) + ([
            "Requested projectile mass differs from the nominal spherical mass; "
            f"projectile density is scaled by {projectile_mass_scale:.6g}."
        ] if abs(projectile_mass_scale - 1.0) > 0.01 else []),
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
    has_armor = case.armor_type != "none"
    armor = config.armors.get(case.armor_type)
    if has_armor and armor is None:
        raise ValueError(f"Unknown armor type: {case.armor_type}")
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
        "*CONTROL_CONTACT",
        # Keep the default penalty scale, request the full initial-penetration
        # check, and use the actual shell section thickness in single-surface contact.
        _line(0.1, 0.0, 2, 0, 1, 0, 1, 0),
        _line(0, 0, 0, 0, 4.0, 1, 0, 0),
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
        _line(
            mesh.history_elements["body_near_impact"],
            mesh.history_elements["body_near_chest"],
            mesh.history_elements["body_near_abdomen"],
            mesh.history_elements["projectile"],
        ),
        "*SECTION_SOLID",
        _line(1, 2),
        "*MAT_VISCOELASTIC",
        _line(
            1,
            config.body.density_kg_m3 / 1.0e9,
            config.body.bulk_modulus_mpa / 1000.0,
            config.body.shear_short_mpa / 1000.0,
            config.body.shear_long_mpa / 1000.0,
            config.body.decay_per_ms,
        ),
    ])
    if has_armor and armor is not None:
        out.extend([
            "*DATABASE_HISTORY_SHELL",
            _line(mesh.history_elements["armor_near_impact"]),
            "*SECTION_SHELL",
            _line(2, 16, 0.833333, 5, 0, 0, 0, 1),
            _line(armor.thickness_mm, armor.thickness_mm, armor.thickness_mm, armor.thickness_mm),
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
        ])
        if 4 in mesh.armor_part_thickness_mm:
            if armor.reinforcement is None:
                raise RuntimeError("Mesh contains iron plates but material is missing")
            reinforcement = armor.reinforcement
            out.extend([
                "*SECTION_SHELL",
                _line(4, 16, 0.833333, 5, 0, 0, 0, 1),
                _line(
                    reinforcement.thickness_mm,
                    reinforcement.thickness_mm,
                    reinforcement.thickness_mm,
                    reinforcement.thickness_mm,
                ),
                "*MAT_PLASTIC_KINEMATIC",
                _line(
                    4,
                    reinforcement.density_kg_m3 / 1.0e9,
                    reinforcement.youngs_modulus_gpa,
                    reinforcement.poisson,
                    reinforcement.yield_mpa / 1000.0,
                    reinforcement.tangent_modulus_mpa / 1000.0,
                    1.0,
                ),
                _line(0.0, 0.0, reinforcement.failure_strain, 0.0),
            ])
    out.extend([
        "*SECTION_SOLID",
        _line(3, 10),
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
    ])
    if has_armor:
        out.extend([
            "*PART",
            f"Armor base - {case.armor_type} ({mesh.armor_part_roles[2]})",
            _line(2, 2, 2, 0, 0, 0, 0, 0),
        ])
        if 4 in mesh.armor_part_thickness_mm:
            out.extend([
                "*PART",
                "Dujeong internal iron plates - discrete sections",
                _line(4, 4, 4, 0, 0, 0, 0, 0),
            ])
    out.extend([
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

    if has_armor:
        out.append("*ELEMENT_SHELL")
        for eid, pid, conn in mesh.armor_elements:
            out.append(_line(eid, pid, *conn))

    contact_part_ids = (
        [1, *sorted(mesh.armor_part_thickness_mm), 3]
        if has_armor
        else [1, 3]
    )
    out.extend([
        "*SET_PART_LIST",
        _line(CONTACT_PART_SET_ID),
        _line(*contact_part_ids),
        "*CONTACT_ERODING_SINGLE_SURFACE",
        # SSTYP=2 makes SSID an explicit part-set ID. This guarantees that the
        # torso, armor (when present), and projectile are in the same contact.
        _line(CONTACT_PART_SET_ID, 0, 2, 0, 0, 0, 0, 0),
        _line(0.15, 0.10, 0.0, 0.0, 10.0, 0, 0.0, 1.0e20),
        _line(1.0, 1.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0),
        _line(0, 1, 1),
        "*INITIAL_VELOCITY_NODE",
    ])
    for nid in iter_projectile_node_ids(mesh):
        out.append(_line(nid, velocity[0], velocity[1], velocity[2], 0.0, 0.0, 0.0, 0))
    out.extend(["*END", ""])
    return "\n".join(out)
