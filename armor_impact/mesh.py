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


def _nearest_index(value: float, minimum: float, span: float, divisions: int) -> int:
    fraction = (value - minimum) / span
    return max(0, min(divisions, round(fraction * divisions)))


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
    armor_bulge_mm: float,
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

    xmin, ymin, zmin = -width_mm / 2, -depth_mm / 2, -height_mm / 2
    dx, dy, dz = width_mm / nx, depth_mm / ny, height_mm / nz

    def body_node_id(i: int, j: int, k: int) -> int:
        return 1 + i + (nx + 1) * (j + (ny + 1) * k)

    for k in range(nz + 1):
        for j in range(ny + 1):
            for i in range(nx + 1):
                nid = body_node_id(i, j, k)
                nodes[nid] = (xmin + i * dx, ymin + j * dy, zmin + k * dz)

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
        for k in range(nz + 1):
            for i in range(nx + 1):
                if i in (0, nx) or k in (0, nz):
                    nid = body_node_id(i, 0, k)
                else:
                    x = xmin + i * dx
                    z = zmin + k * dz
                    shape = math.sin(math.pi * i / nx) * math.sin(math.pi * k / nz)
                    y = ymin - armor_gap_mm - armor_bulge_mm * shape
                    nid = next_node
                    next_node += 1
                    nodes[nid] = (x, y, z)
                armor_node_ids[(i, k)] = nid

        for k in range(nz):
            for i in range(nx):
                conn = (
                    armor_node_ids[(i, k)], armor_node_ids[(i + 1, k)],
                    armor_node_ids[(i + 1, k + 1)], armor_node_ids[(i, k + 1)],
                )
                armor_elements.append((armor_eid, 2, conn))
                armor_element_lookup[(i, k)] = armor_eid
                armor_eid += 1

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

    ix = _nearest_index(impact_x_mm, xmin, width_mm, nx)
    iz = _nearest_index(impact_z_mm, zmin, height_mm, nz)
    chest_iz = _nearest_index(height_mm * 0.20, zmin, height_mm, nz)
    abdomen_iz = _nearest_index(-height_mm * 0.20, zmin, height_mm, nz)
    center_ix = _nearest_index(0.0, xmin, width_mm, nx)
    center_iy = _nearest_index(0.0, ymin, depth_mm, ny)
    center_iz = _nearest_index(0.0, zmin, height_mm, nz)

    sensors = {
        "impact_front": body_node_id(ix, 0, iz),
        "impact_back": body_node_id(ix, ny, iz),
        "chest_front": body_node_id(center_ix, 0, chest_iz),
        "chest_back": body_node_id(center_ix, ny, chest_iz),
        "abdomen_front": body_node_id(center_ix, 0, abdomen_iz),
        "abdomen_back": body_node_id(center_ix, ny, abdomen_iz),
        "torso_center": body_node_id(center_ix, center_iy, center_iz),
        "projectile_center": projectile_center_id,
    }
    if include_armor:
        sensors["armor_near_impact"] = armor_node_ids[(ix, iz)]
    history_elements = {
        "body_near_impact": element_lookup[(min(ix, nx - 1), 0, min(iz, nz - 1))],
        "body_near_chest": element_lookup[(min(center_ix, nx - 1), 0, min(chest_iz, nz - 1))],
        "body_near_abdomen": element_lookup[(min(center_ix, nx - 1), 0, min(abdomen_iz, nz - 1))],
        "projectile": projectile_elements[0][0],
    }
    if include_armor:
        history_elements["armor_near_impact"] = armor_element_lookup[(min(ix, nx - 1), min(iz, nz - 1))]
    return MeshModel(
        nodes=nodes,
        body_elements=body_elements,
        armor_elements=armor_elements,
        projectile_elements=projectile_elements,
        sensors=sensors,
        history_elements=history_elements,
        projectile_mesh_volume_mm3=projectile_volume,
    )


def iter_projectile_node_ids(mesh: MeshModel) -> Iterable[int]:
    return (nid for nid in mesh.nodes if nid >= 2_000_001)
