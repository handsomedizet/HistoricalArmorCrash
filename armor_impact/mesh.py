from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


Vec3 = tuple[float, float, float]


@dataclass
class MeshModel:
    nodes: dict[int, Vec3]
    body_elements: list[tuple[int, int, tuple[int, ...]]]
    armor_elements: list[tuple[int, int, tuple[int, ...]]]
    projectile_elements: list[tuple[int, int, tuple[int, ...]]]
    sensors: dict[str, int]
    history_elements: dict[str, int]
    projectile_mesh_volume_mm3: float
    armor_torso_node_pairs: list[tuple[int, int]]
    armor_part_thickness_mm: dict[int, float]
    armor_part_roles: dict[int, str]
    armor_area_by_part_mm2: dict[int, float]


def _nearest_index(value: float, minimum: float, span: float, divisions: int) -> int:
    fraction = (value - minimum) / span
    return max(0, min(divisions, round(fraction * divisions)))


def _square_to_ellipse(
    u: float, v: float, semi_width_mm: float, semi_depth_mm: float
) -> tuple[float, float]:
    """Map a structured square grid to a smooth elliptical torso cross-section."""
    return (
        semi_width_mm * u * math.sqrt(max(0.0, 1.0 - v * v / 2.0)),
        semi_depth_mm * v * math.sqrt(max(0.0, 1.0 - u * u / 2.0)),
    )


def _armor_boundary_path(nx: int, ny: int, wrap_mode: str) -> tuple[list[tuple[int, int]], bool]:
    """Return counter-clockwise torso boundary indices, starting at the left flank."""
    mid = ny // 2
    path: list[tuple[int, int]] = [(0, mid)]
    path.extend((0, j) for j in range(mid - 1, -1, -1))
    path.extend((i, 0) for i in range(1, nx + 1))
    if wrap_mode == "front_half":
        path.extend((nx, j) for j in range(1, mid + 1))
        return path, False
    if wrap_mode != "full_wrap":
        raise ValueError(f"Unsupported armor wrap_mode: {wrap_mode}")
    path.extend((nx, j) for j in range(1, ny + 1))
    path.extend((i, ny) for i in range(nx - 1, -1, -1))
    path.extend((0, j) for j in range(ny - 1, mid, -1))
    return path, True


def _quad_area(nodes: dict[int, Vec3], conn: tuple[int, ...]) -> float:
    def triangle(a: Vec3, b: Vec3, c: Vec3) -> float:
        ab = tuple(b[i] - a[i] for i in range(3))
        ac = tuple(c[i] - a[i] for i in range(3))
        cross = (
            ab[1] * ac[2] - ab[2] * ac[1],
            ab[2] * ac[0] - ab[0] * ac[2],
            ab[0] * ac[1] - ab[1] * ac[0],
        )
        return math.sqrt(sum(value * value for value in cross)) / 2.0

    a, b, c, d = (nodes[nid] for nid in conn)
    return triangle(a, b, c) + triangle(a, c, d)


def _signed_tet_volume(a: Vec3, b: Vec3, c: Vec3, d: Vec3) -> float:
    ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    ac = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    ad = (d[0] - a[0], d[1] - a[1], d[2] - a[2])
    cross = (
        ac[1] * ad[2] - ac[2] * ad[1],
        ac[2] * ad[0] - ac[0] * ad[2],
        ac[0] * ad[1] - ac[1] * ad[0],
    )
    return (ab[0] * cross[0] + ab[1] * cross[1] + ab[2] * cross[2]) / 6.0


def _icosphere(subdivisions: int) -> tuple[list[Vec3], list[tuple[int, int, int]]]:
    phi = (1.0 + math.sqrt(5.0)) / 2.0
    vertices: list[Vec3] = [
        (-1, phi, 0), (1, phi, 0), (-1, -phi, 0), (1, -phi, 0),
        (0, -1, phi), (0, 1, phi), (0, -1, -phi), (0, 1, -phi),
        (phi, 0, -1), (phi, 0, 1), (-phi, 0, -1), (-phi, 0, 1),
    ]
    faces = [
        (0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
        (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
        (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
        (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1),
    ]

    def normalize(v: Vec3) -> Vec3:
        length = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
        return (v[0] / length, v[1] / length, v[2] / length)

    vertices = [normalize(v) for v in vertices]
    for _ in range(subdivisions):
        midpoint_cache: dict[tuple[int, int], int] = {}

        def midpoint(i: int, j: int) -> int:
            edge = (min(i, j), max(i, j))
            if edge in midpoint_cache:
                return midpoint_cache[edge]
            a, b = vertices[i], vertices[j]
            index = len(vertices)
            vertices.append(normalize(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2)))
            midpoint_cache[edge] = index
            return index

        refined: list[tuple[int, int, int]] = []
        for a, b, c in faces:
            ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
            refined.extend([(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)])
        faces = refined
    return vertices, faces


def build_mesh(
    *,
    width_mm: float,
    depth_mm: float,
    height_mm: float,
    nx: int,
    ny: int,
    nz: int,
    armor_gap_mm: float,
    armor_shell_thickness_mm: float,
    armor_construction: str,
    armor_wrap_mode: str,
    reinforcement_thickness_mm: float | None,
    plate_width_mm: float | None,
    plate_height_mm: float | None,
    plate_gap_mm: float | None,
    projectile_center: Vec3,
    projectile_radius_mm: float,
    projectile_subdivisions: int,
    impact_x_mm: float,
    impact_z_mm: float,
    include_armor: bool = True,
) -> MeshModel:
    nodes: dict[int, Vec3] = {}
    body_elements: list[tuple[int, int, tuple[int, ...]]] = []
    armor_elements: list[tuple[int, int, tuple[int, ...]]] = []
    projectile_elements: list[tuple[int, int, tuple[int, ...]]] = []
    armor_torso_node_pairs: list[tuple[int, int]] = []
    armor_part_thickness_mm: dict[int, float] = {}
    armor_part_roles: dict[int, str] = {}
    armor_area_by_part_mm2: dict[int, float] = {}

    if armor_gap_mm < 0:
        raise ValueError("armor_gap_mm is a physical surface clearance and cannot be negative")
    if include_armor and armor_shell_thickness_mm <= 0:
        raise ValueError("armor_shell_thickness_mm must be positive when armor is enabled")

    xmin, ymin, zmin = -width_mm / 2, -depth_mm / 2, -height_mm / 2
    dz = height_mm / nz

    def body_node_id(i: int, j: int, k: int) -> int:
        return 1 + i + (nx + 1) * (j + (ny + 1) * k)

    # The prior rectangular block artificially tied a flat armor panel to a flat
    # anterior face. A smooth square-to-ellipse map retains a structured solid mesh
    # while producing a human-like oval cross-section with distinct flanks.
    for k in range(nz + 1):
        for j in range(ny + 1):
            for i in range(nx + 1):
                nid = body_node_id(i, j, k)
                u = -1.0 + 2.0 * i / nx
                v = -1.0 + 2.0 * j / ny
                x, y = _square_to_ellipse(u, v, width_mm / 2.0, depth_mm / 2.0)
                z = zmin + k * dz
                nodes[nid] = (x, y, z)

    eid = 1
    element_lookup: dict[tuple[int, int, int], int] = {}
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                conn = (
                    body_node_id(i, j, k), body_node_id(i + 1, j, k),
                    body_node_id(i + 1, j + 1, k), body_node_id(i, j + 1, k),
                    body_node_id(i, j, k + 1), body_node_id(i + 1, j, k + 1),
                    body_node_id(i + 1, j + 1, k + 1), body_node_id(i, j + 1, k + 1),
                )
                body_elements.append((eid, 1, conn))
                element_lookup[(i, j, k)] = eid
                eid += 1

    next_node = max(nodes) + 1
    armor_node_ids: dict[tuple[int, int], int] = {}
    armor_eid = 1_000_001
    armor_element_lookup: dict[tuple[int, int], int] = {}
    if include_armor:
        if armor_construction not in {"continuous_plate", "riveted_discrete_plates"}:
            raise ValueError(f"Unsupported armor construction: {armor_construction}")
        if armor_construction == "riveted_discrete_plates":
            if reinforcement_thickness_mm is None or reinforcement_thickness_mm <= 0:
                raise ValueError("Discrete armor requires a positive reinforcement thickness")
            if not all(
                value is not None and value > 0
                for value in (plate_width_mm, plate_height_mm, plate_gap_mm)
            ):
                raise ValueError("Discrete armor requires positive plate dimensions and gap")
        reference_thickness = max(
            armor_shell_thickness_mm,
            reinforcement_thickness_mm or 0.0,
        )
        armor_midplane_offset_mm = armor_gap_mm + reference_thickness / 2.0
        boundary_path, closed_wrap = _armor_boundary_path(nx, ny, armor_wrap_mode)
        for k in range(nz + 1):
            for path_index, (i, j) in enumerate(boundary_path):
                body_nid = body_node_id(i, j, k)
                x, y, z = nodes[body_nid]
                nx_out = x / ((width_mm / 2.0) ** 2)
                ny_out = y / ((depth_mm / 2.0) ** 2)
                normal_length = math.hypot(nx_out, ny_out)
                if normal_length <= 0:
                    raise RuntimeError("Could not determine outward torso normal")
                nx_out /= normal_length
                ny_out /= normal_length
                nid = next_node
                next_node += 1
                nodes[nid] = (
                    x + nx_out * armor_midplane_offset_mm,
                    y + ny_out * armor_midplane_offset_mm,
                    z,
                )
                armor_node_ids[(path_index, k)] = nid
                armor_torso_node_pairs.append((nid, body_nid))

        path_segments = len(boundary_path) if closed_wrap else len(boundary_path) - 1
        nominal_path_step = sum(
            math.dist(
                nodes[armor_node_ids[(p, 0)]],
                nodes[armor_node_ids[((p + 1) % len(boundary_path), 0)]],
            )
            for p in range(path_segments)
        ) / max(1, path_segments)
        plate_cols = max(1, round((plate_width_mm or nominal_path_step) / nominal_path_step))
        gap_cols = max(1, round((plate_gap_mm or nominal_path_step) / nominal_path_step))
        plate_rows = max(1, round((plate_height_mm or dz) / dz))
        gap_rows = max(1, round((plate_gap_mm or dz) / dz))
        period_cols = plate_cols + gap_cols
        period_rows = plate_rows + gap_rows

        # Phase the repeating studded-plate layout so a centred shot represents a
        # hit on an iron plate, not a conveniently selected textile gap.
        impact_segment = min(
            range(path_segments),
            key=lambda p: (
                (sum(nodes[armor_node_ids[(q, 0)]][0] for q in (p, (p + 1) % len(boundary_path))) / 2.0
                 - impact_x_mm) ** 2
                + (sum(nodes[armor_node_ids[(q, 0)]][1] for q in (p, (p + 1) % len(boundary_path))) / 2.0
                 + depth_mm / 2.0) ** 2
            ),
        )
        impact_row = min(nz - 1, max(0, _nearest_index(impact_z_mm, zmin, height_mm, nz)))

        for k in range(nz):
            for path_index in range(path_segments):
                next_path_index = (path_index + 1) % len(boundary_path)
                conn = (
                    armor_node_ids[(path_index, k)],
                    armor_node_ids[(next_path_index, k)],
                    armor_node_ids[(next_path_index, k + 1)],
                    armor_node_ids[(path_index, k + 1)],
                )
                pid = 2
                if armor_construction == "riveted_discrete_plates":
                    col_phase = (path_index - impact_segment + plate_cols // 2) % period_cols
                    row_phase = (k - impact_row + plate_rows // 2) % period_rows
                    if col_phase < plate_cols and row_phase < plate_rows:
                        pid = 4
                armor_elements.append((armor_eid, pid, conn))
                armor_element_lookup[(path_index, k)] = armor_eid
                armor_area_by_part_mm2[pid] = (
                    armor_area_by_part_mm2.get(pid, 0.0) + _quad_area(nodes, conn)
                )
                armor_eid += 1

        armor_part_thickness_mm[2] = armor_shell_thickness_mm
        armor_part_roles[2] = (
            "continuous_plate" if armor_construction == "continuous_plate" else "textile_gap"
        )
        if armor_construction == "riveted_discrete_plates":
            armor_part_thickness_mm[4] = float(reinforcement_thickness_mm)
            armor_part_roles[4] = "internal_iron_plate"

    sphere_vertices, sphere_faces = _icosphere(projectile_subdivisions)
    projectile_center_id = 2_000_001
    nodes[projectile_center_id] = projectile_center
    sphere_node_ids: list[int] = []
    for index, unit in enumerate(sphere_vertices, start=1):
        nid = projectile_center_id + index
        sphere_node_ids.append(nid)
        nodes[nid] = (
            projectile_center[0] + unit[0] * projectile_radius_mm,
            projectile_center[1] + unit[1] * projectile_radius_mm,
            projectile_center[2] + unit[2] * projectile_radius_mm,
        )

    projectile_eid = 2_000_001
    projectile_volume = 0.0
    center = nodes[projectile_center_id]
    for fa, fb, fc in sphere_faces:
        n1, n2, n3 = sphere_node_ids[fa], sphere_node_ids[fb], sphere_node_ids[fc]
        signed = _signed_tet_volume(center, nodes[n1], nodes[n2], nodes[n3])
        if signed < 0:
            n2, n3 = n3, n2
            signed = -signed
        conn = (projectile_center_id, n1, n2, n3, n3, n3, n3, n3, 0, 0)
        projectile_elements.append((projectile_eid, 3, conn))
        projectile_eid += 1
        projectile_volume += signed

    iz = _nearest_index(impact_z_mm, zmin, height_mm, nz)
    chest_iz = _nearest_index(height_mm * 0.20, zmin, height_mm, nz)
    abdomen_iz = _nearest_index(-height_mm * 0.20, zmin, height_mm, nz)
    front_indices = [(i, 0) for i in range(nx + 1)]
    front_indices.extend((0, j) for j in range(1, ny // 2 + 1))
    front_indices.extend((nx, j) for j in range(1, ny // 2 + 1))

    def nearest_front_index(x_target: float) -> tuple[int, int]:
        return min(
            front_indices,
            key=lambda ij: (
                (nodes[body_node_id(ij[0], ij[1], iz)][0] - x_target) ** 2
                + max(0.0, nodes[body_node_id(ij[0], ij[1], iz)][1]) ** 2
            ),
        )

    ix, impact_j = nearest_front_index(impact_x_mm)
    center_ix, center_front_j = nearest_front_index(0.0)
    impact_back_ix, impact_back_j = ix, ny - impact_j
    center_back_ix, center_back_j = center_ix, ny - center_front_j
    center_iy = _nearest_index(0.0, ymin, depth_mm, ny)
    center_iz = _nearest_index(0.0, zmin, height_mm, nz)

    sensors = {
        "impact_front": body_node_id(ix, impact_j, iz),
        "impact_back": body_node_id(impact_back_ix, impact_back_j, iz),
        "chest_front": body_node_id(center_ix, center_front_j, chest_iz),
        "chest_back": body_node_id(center_back_ix, center_back_j, chest_iz),
        "abdomen_front": body_node_id(center_ix, center_front_j, abdomen_iz),
        "abdomen_back": body_node_id(center_back_ix, center_back_j, abdomen_iz),
        "torso_center": body_node_id(center_ix, center_iy, center_iz),
        "projectile_center": projectile_center_id,
    }
    if include_armor:
        impact_armor_node = min(
            armor_node_ids.values(),
            key=lambda nid: (
                (mesh_x := nodes[nid][0]) - impact_x_mm
            ) ** 2 + (nodes[nid][2] - impact_z_mm) ** 2 + max(0.0, nodes[nid][1]) ** 2,
        )
        sensors["armor_near_impact"] = impact_armor_node
    history_elements = {
        "body_near_impact": element_lookup[(min(ix, nx - 1), min(impact_j, ny - 1), min(iz, nz - 1))],
        "body_near_chest": element_lookup[(min(center_ix, nx - 1), min(center_front_j, ny - 1), min(chest_iz, nz - 1))],
        "body_near_abdomen": element_lookup[(min(center_ix, nx - 1), min(center_front_j, ny - 1), min(abdomen_iz, nz - 1))],
        "projectile": projectile_elements[0][0],
    }
    if include_armor:
        impact_armor_element = min(
            armor_elements,
            key=lambda item: sum(
                (sum(nodes[nid][axis] for nid in item[2]) / 4.0 - target) ** 2
                for axis, target in ((0, impact_x_mm), (2, impact_z_mm))
            ) + max(0.0, sum(nodes[nid][1] for nid in item[2]) / 4.0) ** 2,
        )
        history_elements["armor_near_impact"] = impact_armor_element[0]
    return MeshModel(
        nodes=nodes,
        body_elements=body_elements,
        armor_elements=armor_elements,
        projectile_elements=projectile_elements,
        sensors=sensors,
        history_elements=history_elements,
        projectile_mesh_volume_mm3=projectile_volume,
        armor_torso_node_pairs=armor_torso_node_pairs,
        armor_part_thickness_mm=armor_part_thickness_mm,
        armor_part_roles=armor_part_roles,
        armor_area_by_part_mm2=armor_area_by_part_mm2,
    )


def iter_projectile_node_ids(mesh: MeshModel) -> Iterable[int]:
    return (nid for nid in mesh.nodes if nid >= 2_000_001)
