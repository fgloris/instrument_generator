#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Blender 5.2 laboratory-asset toolkit.

The module is instrument-agnostic. It provides reusable geometry, volume,
graduation, glass-material, studio-lighting, camera, and rendering helpers.
All low-level geometric values use Blender metres; use ``mm()`` for readable
millimetre specifications.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

import bmesh
import bpy
from mathutils import Vector


@dataclass(frozen=True)
class ProfilePoint:
    radius: float
    z: float
    deform_weight: float = 0.0


@dataclass(frozen=True)
class GraduationStyle:
    end_angle_deg: float = -34.0
    major_length: float = 0.016
    minor_length: float = 0.008
    line_radius: float = 0.00028
    surface_clearance: float = 0.00001
    label_tangent_offset: float = 0.0055
    text_size: float = 0.0044
    text_extrude: float = 0.000012
    text_outline: float = 0.000006
    text_surface_clearance: float = 0.000008
    text_curve_resolution: int = 12
    text_subdivision_cuts: int = 1
    top_clearance: float = 0.003
    arc_samples: int = 24


@dataclass(frozen=True)
class RenderView:
    name: str
    azimuth_deg: float
    elevation_deg: float
    distance: float
    lens_mm: float = 62.0


RingDeformer = Callable[[float, float, float, float], tuple[float, float]]


def mm(value: float) -> float:
    return value / 1000.0


def ml_to_m3(value_ml: float) -> float:
    return value_ml * 1e-6


def m3_to_ml(value_m3: float) -> float:
    return value_m3 * 1e6


def _positive_whole_ml(value: int | float, *, name: str) -> int:
    """Normalize a positive whole-millilitre value for range/modulo logic.

    Instrument specifications are parsed through Pydantic and may therefore
    arrive as integer-valued floats (for example ``250.0``).  Python's
    ``range`` rejects floats even when they represent exact integers, so the
    toolkit normalizes them at its public API boundary instead of requiring
    every generated script to reproduce a fragile cast.
    """

    if isinstance(value, bool):
        raise TypeError(f"{name} must be a number, not bool.")
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TypeError(f"{name} must be a positive whole number of millilitres.") from exc
    if not math.isfinite(numeric) or numeric <= 0.0 or not numeric.is_integer():
        raise ValueError(f"{name} must be a positive whole number of millilitres.")
    return int(numeric)


def profile_from_mm(rows: Iterable[Sequence[float]]) -> list[ProfilePoint]:
    points: list[ProfilePoint] = []
    for row in rows:
        if len(row) == 2:
            radius_mm, z_mm = row
            weight = 0.0
        elif len(row) == 3:
            radius_mm, z_mm, weight = row
        else:
            raise ValueError("Each profile row must contain 2 or 3 values.")
        points.append(ProfilePoint(mm(float(radius_mm)), mm(float(z_mm)), float(weight)))
    validate_profile(points)
    return points


def validate_profile(profile: Sequence[ProfilePoint]) -> None:
    if len(profile) < 2:
        raise ValueError("A profile requires at least two points.")
    previous_z = -math.inf
    for point in profile:
        if point.radius <= 0.0:
            raise ValueError("Profile radii must be positive.")
        if point.z <= previous_z:
            raise ValueError("Profile heights must be strictly increasing.")
        previous_z = point.z


def clear_scene() -> None:
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)

    for collection in list(bpy.data.collections):
        bpy.data.collections.remove(collection)

    for data_blocks in (
        bpy.data.meshes,
        bpy.data.curves,
        bpy.data.materials,
        bpy.data.cameras,
        bpy.data.lights,
    ):
        for data_block in list(data_blocks):
            if data_block.users == 0:
                data_blocks.remove(data_block)


def create_collection(name: str) -> bpy.types.Collection:
    collection = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(collection)
    return collection


def select_only(obj: bpy.types.Object) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def assign_metadata(obj: bpy.types.Object, metadata: Mapping[str, object]) -> None:
    for key, value in metadata.items():
        obj[key] = value


def sample_profile(profile: Sequence[ProfilePoint], z: float) -> ProfilePoint:
    validate_profile(profile)
    if z <= profile[0].z:
        return profile[0]
    if z >= profile[-1].z:
        return profile[-1]

    for lower, upper in zip(profile[:-1], profile[1:]):
        if lower.z <= z <= upper.z:
            t = (z - lower.z) / (upper.z - lower.z)
            return ProfilePoint(
                radius=lower.radius + (upper.radius - lower.radius) * t,
                z=z,
                deform_weight=(
                    lower.deform_weight
                    + (upper.deform_weight - lower.deform_weight) * t
                ),
            )
    raise RuntimeError("Profile interpolation failed.")


def frustum_segment_volume_m3(lower: ProfilePoint, upper: ProfilePoint) -> float:
    height = upper.z - lower.z
    return (
        math.pi
        * height
        * (
            lower.radius * lower.radius
            + lower.radius * upper.radius
            + upper.radius * upper.radius
        )
        / 3.0
    )


def _partial_segment_volume_m3(
    lower: ProfilePoint,
    upper: ProfilePoint,
    local_height: float,
) -> float:
    segment_height = upper.z - lower.z
    x = min(max(local_height, 0.0), segment_height)
    slope = (upper.radius - lower.radius) / segment_height
    return math.pi * (
        lower.radius * lower.radius * x
        + lower.radius * slope * x * x
        + slope * slope * x * x * x / 3.0
    )


def profile_capacity_m3(profile: Sequence[ProfilePoint]) -> float:
    validate_profile(profile)
    return sum(
        frustum_segment_volume_m3(lower, upper)
        for lower, upper in zip(profile[:-1], profile[1:])
    )


def profile_capacity_ml(profile: Sequence[ProfilePoint]) -> float:
    return m3_to_ml(profile_capacity_m3(profile))


def volume_below_height_m3(profile: Sequence[ProfilePoint], z: float) -> float:
    validate_profile(profile)
    if z <= profile[0].z:
        return 0.0

    volume = 0.0
    for lower, upper in zip(profile[:-1], profile[1:]):
        if z >= upper.z:
            volume += frustum_segment_volume_m3(lower, upper)
            continue
        if z > lower.z:
            volume += _partial_segment_volume_m3(lower, upper, z - lower.z)
        break
    return volume


def height_for_volume_ml(
    profile: Sequence[ProfilePoint],
    target_volume_ml: float,
    iterations: int = 64,
) -> float:
    """Invert a piecewise-linear radius profile to find a volume height.

    Each adjacent profile pair forms an exact frustum segment. The function
    first locates the segment containing the target cumulative volume, then
    solves the cubic partial-volume relation by stable bisection. This avoids
    assuming a constant-radius cylinder and remains valid for tapered vessels.
    """

    if target_volume_ml < 0.0:
        raise ValueError("Target volume cannot be negative.")

    target = ml_to_m3(target_volume_ml)
    capacity = profile_capacity_m3(profile)
    if target > capacity + 1e-12:
        raise ValueError(
            f"Requested {target_volume_ml:g} mL exceeds profile capacity "
            f"{m3_to_ml(capacity):.3f} mL."
        )
    if target == 0.0:
        return profile[0].z

    accumulated = 0.0
    for lower, upper in zip(profile[:-1], profile[1:]):
        segment_volume = frustum_segment_volume_m3(lower, upper)
        if accumulated + segment_volume < target:
            accumulated += segment_volume
            continue

        local_target = target - accumulated
        low = 0.0
        high = upper.z - lower.z
        for _ in range(iterations):
            middle = 0.5 * (low + high)
            partial = _partial_segment_volume_m3(lower, upper, middle)
            if partial < local_target:
                low = middle
            else:
                high = middle
        return lower.z + 0.5 * (low + high)

    return profile[-1].z


def graduation_levels(
    inner_profile: Sequence[ProfilePoint],
    maximum_volume_ml: int | float,
    interval_ml: int | float,
    top_clearance: float = 0.0,
) -> list[tuple[int, float]]:
    maximum_volume_ml = _positive_whole_ml(
        maximum_volume_ml,
        name="maximum_volume_ml",
    )
    interval_ml = _positive_whole_ml(interval_ml, name="interval_ml")

    maximum_z = inner_profile[-1].z - top_clearance
    levels: list[tuple[int, float]] = []
    # ``maximum_volume_ml + 1`` includes an exact final mark while never
    # generating a mark above the requested maximum when it is not divisible
    # by the interval.
    for value_ml in range(interval_ml, maximum_volume_ml + 1, interval_ml):
        z = height_for_volume_ml(inner_profile, value_ml)
        if z <= maximum_z:
            levels.append((value_ml, z))
    return levels


def angular_difference(angle: float, center: float) -> float:
    return math.atan2(math.sin(angle - center), math.cos(angle - center))


def cosine_lobe(theta: float, center: float, half_width: float) -> float:
    distance = abs(angular_difference(theta, center))
    if distance >= half_width:
        return 0.0
    normalized = distance / half_width
    return math.cos(normalized * math.pi / 2.0) ** 2


def make_angular_deformer(
    center_deg: float,
    half_width_deg: float,
    radial_extension: float,
    vertical_rise: float = 0.0,
) -> RingDeformer:
    center = math.radians(center_deg)
    half_width = math.radians(half_width_deg)

    def deform(theta: float, radius: float, z: float, weight: float) -> tuple[float, float]:
        shape = cosine_lobe(theta, center, half_width) * weight
        return radius + radial_extension * shape, z + vertical_rise * shape

    return deform


def identity_deformer(
    theta: float,
    radius: float,
    z: float,
    weight: float,
) -> tuple[float, float]:
    del theta, weight
    return radius, z


def surface_point_at(
    outer_profile: Sequence[ProfilePoint],
    z: float,
    theta: float,
    deformer: RingDeformer | None = None,
) -> tuple[float, float]:
    sample = sample_profile(outer_profile, z)
    transform = deformer or identity_deformer
    return transform(theta, sample.radius, sample.z, sample.deform_weight)


def surface_radius_at(
    outer_profile: Sequence[ProfilePoint],
    z: float,
    theta: float,
    deformer: RingDeformer | None = None,
) -> float:
    radius, _ = surface_point_at(outer_profile, z, theta, deformer)
    return radius


def _add_profile_ring(
    vertices: list[tuple[float, float, float]],
    point: ProfilePoint,
    radial_segments: int,
    deformer: RingDeformer,
) -> list[int]:
    indices: list[int] = []
    for index in range(radial_segments):
        theta = 2.0 * math.pi * index / radial_segments
        radius, z = deformer(theta, point.radius, point.z, point.deform_weight)
        indices.append(len(vertices))
        vertices.append((radius * math.cos(theta), radius * math.sin(theta), z))
    return indices


def _connect_rings(
    faces: list[tuple[int, ...]],
    lower: Sequence[int],
    upper: Sequence[int],
    inward: bool,
) -> None:
    count = len(lower)
    for index in range(count):
        nxt = (index + 1) % count
        if inward:
            faces.append((lower[index], upper[index], upper[nxt], lower[nxt]))
        else:
            faces.append((lower[index], lower[nxt], upper[nxt], upper[index]))


def create_hollow_revolved_mesh(
    name: str,
    outer_profile: Sequence[ProfilePoint],
    inner_profile: Sequence[ProfilePoint],
    collection: bpy.types.Collection,
    material: bpy.types.Material | None = None,
    radial_segments: int = 192,
    deformer: RingDeformer | None = None,
) -> bpy.types.Object:
    """Build a closed hollow vessel directly from two vertical profiles.

    Separate outer and inner profile rings create real wall thickness. Their
    winding is reversed so outer normals face the room and inner normals face
    the cavity. The top rings form a solid rim, while two triangle fans close
    the external base and internal floor. A deformer can alter each ring point
    by angle and profile weight without changing the generic mesh algorithm.
    """

    validate_profile(outer_profile)
    validate_profile(inner_profile)
    if radial_segments < 12:
        raise ValueError("radial_segments must be at least 12.")

    transform = deformer or identity_deformer
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []

    outer_rings = [
        _add_profile_ring(vertices, point, radial_segments, transform)
        for point in outer_profile
    ]
    inner_rings = [
        _add_profile_ring(vertices, point, radial_segments, transform)
        for point in inner_profile
    ]

    for lower, upper in zip(outer_rings[:-1], outer_rings[1:]):
        _connect_rings(faces, lower, upper, inward=False)
    for lower, upper in zip(inner_rings[:-1], inner_rings[1:]):
        _connect_rings(faces, lower, upper, inward=True)

    outer_top = outer_rings[-1]
    inner_top = inner_rings[-1]
    for index in range(radial_segments):
        nxt = (index + 1) % radial_segments
        faces.append((outer_top[index], outer_top[nxt], inner_top[nxt], inner_top[index]))

    outer_bottom_center = len(vertices)
    vertices.append((0.0, 0.0, outer_profile[0].z))
    outer_bottom = outer_rings[0]
    for index in range(radial_segments):
        nxt = (index + 1) % radial_segments
        faces.append((outer_bottom_center, outer_bottom[nxt], outer_bottom[index]))

    inner_floor_center = len(vertices)
    vertices.append((0.0, 0.0, inner_profile[0].z))
    inner_bottom = inner_rings[0]
    for index in range(radial_segments):
        nxt = (index + 1) % radial_segments
        faces.append((inner_floor_center, inner_bottom[index], inner_bottom[nxt]))

    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.validate(verbose=False)
    mesh.update(calc_edges=True)

    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    if material is not None:
        mesh.materials.append(material)
    return obj


def _new_principled_material(
    name: str,
    base_color: tuple[float, float, float, float],
    roughness: float,
) -> bpy.types.Material:
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (0, 0)
    principled.inputs["Base Color"].default_value = base_color
    principled.inputs["Roughness"].default_value = roughness

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (320, 0)
    links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    return material


def _new_emission_material(
    name: str,
    color: tuple[float, float, float, float],
    strength: float = 1.0,
) -> bpy.types.Material:
    """Create a camera-visible, shadeless material for reference graphics."""

    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    emission = nodes.new("ShaderNodeEmission")
    emission.location = (0, 0)
    emission.inputs["Color"].default_value = color
    emission.inputs["Strength"].default_value = strength

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (320, 0)
    links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return material


def create_borosilicate_glass_material(
    name: str = "MAT_Borosilicate_Glass",
    tint: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0),
    roughness: float = 0.014,
    ior: float = 1.47,
    absorption_density: float = 0.030,
) -> bpy.types.Material:
    """Create clear borosilicate glass suited to restrained studio rendering.

    The material stays physically glass-like through transmission and IOR, while
    a very light volume absorption gives thick areas such as the base and rim a
    readable body. Roughness remains low so the vessel keeps crisp strip-light
    highlights without sliding into a mirror-like chrome appearance.
    """

    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (0, 20)
    principled.inputs["Base Color"].default_value = tint
    principled.inputs["Roughness"].default_value = roughness
    principled.inputs["IOR"].default_value = ior
    principled.inputs["Transmission Weight"].default_value = 1.0
    principled.inputs["Alpha"].default_value = 1.0

    absorption = nodes.new("ShaderNodeVolumeAbsorption")
    absorption.location = (0, -180)
    absorption.inputs["Color"].default_value = (0.97, 0.985, 1.0, 1.0)
    absorption.inputs["Density"].default_value = absorption_density

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (320, 0)
    links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    links.new(absorption.outputs["Volume"], output.inputs["Volume"])
    return material


def create_marking_material(name: str = "MAT_Graduation_White") -> bpy.types.Material:
    return _new_principled_material(name, (0.92, 0.94, 0.97, 1.0), 0.28)


def create_floor_material(name: str = "MAT_Studio_Floor") -> bpy.types.Material:
    # A rough mid-dark floor avoids a white specular wash while preserving
    # enough background contrast to reveal the silhouette of clear glass.
    return _new_principled_material(name, (0.20, 0.205, 0.215, 1.0), 0.93)


def create_minor_grid_material(name: str = "MAT_Studio_Grid_Minor") -> bpy.types.Material:
    # Emission strength 1 makes the grid camera-visible and independent of lamps.
    return _new_emission_material(name, (0.050, 0.058, 0.072, 1.0), 0.82)


def create_major_grid_material(name: str = "MAT_Studio_Grid_Major") -> bpy.types.Material:
    return _new_emission_material(name, (0.016, 0.020, 0.030, 1.0), 0.92)


def create_curve_polyline(
    name: str,
    points: Sequence[Vector],
    bevel_radius: float,
    material: bpy.types.Material,
    collection: bpy.types.Collection,
) -> bpy.types.Object:
    curve_data = bpy.data.curves.new(name=f"{name}_Curve", type="CURVE")
    curve_data.dimensions = "3D"
    curve_data.fill_mode = "FULL"
    curve_data.resolution_u = 2
    curve_data.bevel_depth = bevel_radius
    curve_data.bevel_resolution = 3

    spline = curve_data.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for point, coordinate in zip(spline.points, points):
        point.co = (*coordinate, 1.0)

    obj = bpy.data.objects.new(name, curve_data)
    collection.objects.link(obj)
    curve_data.materials.append(material)
    return obj


def create_surface_arc_tick(
    name: str,
    z: float,
    physical_length: float,
    outer_profile: Sequence[ProfilePoint],
    style: GraduationStyle,
    material: bpy.types.Material,
    collection: bpy.types.Collection,
    outer_deformer: RingDeformer | None = None,
) -> bpy.types.Object:
    """Create a tick that follows the actual vessel surface along its full arc."""

    end_angle = math.radians(style.end_angle_deg)
    reference_radius = surface_radius_at(outer_profile, z, end_angle, outer_deformer)
    centreline_reference = reference_radius + style.line_radius + style.surface_clearance
    angular_length = physical_length / centreline_reference
    start_angle = end_angle - angular_length

    points: list[Vector] = []
    for index in range(style.arc_samples):
        t = index / (style.arc_samples - 1)
        theta = start_angle + (end_angle - start_angle) * t
        wall_radius, surface_z = surface_point_at(
            outer_profile, z, theta, outer_deformer
        )
        centreline_radius = wall_radius + style.line_radius + style.surface_clearance
        points.append(
            Vector(
                (
                    centreline_radius * math.cos(theta),
                    centreline_radius * math.sin(theta),
                    surface_z,
                )
            )
        )

    return create_curve_polyline(
        name=name,
        points=points,
        bevel_radius=style.line_radius,
        material=material,
        collection=collection,
    )


def _font_curve_to_mesh(
    name: str,
    text: str,
    style: GraduationStyle,
    collection: bpy.types.Collection,
) -> bpy.types.Object:
    text_data = bpy.data.curves.new(name=f"{name}_Font", type="FONT")
    text_data.body = text
    text_data.align_x = "LEFT"
    text_data.align_y = "CENTER"
    text_data.size = style.text_size
    text_data.extrude = style.text_extrude
    text_data.offset = style.text_outline
    text_data.resolution_u = style.text_curve_resolution

    source = bpy.data.objects.new(f"{name}_Source", text_data)
    collection.objects.link(source)
    bpy.context.view_layer.update()

    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = source.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(evaluated, depsgraph=depsgraph)
    result = bpy.data.objects.new(name, mesh)
    collection.objects.link(result)

    bpy.data.objects.remove(source, do_unlink=True)
    if text_data.users == 0:
        bpy.data.curves.remove(text_data)
    return result


def _subdivide_label_mesh(mesh: bpy.types.Mesh, cuts: int) -> None:
    if cuts <= 0:
        return

    bm = bmesh.new()
    bm.from_mesh(mesh)
    if bm.faces:
        bmesh.ops.triangulate(bm, faces=list(bm.faces))
    if bm.edges:
        bmesh.ops.subdivide_edges(
            bm,
            edges=list(bm.edges),
            cuts=cuts,
            use_grid_fill=True,
        )
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()


def create_conformal_surface_label(
    name: str,
    text: str,
    z: float,
    outer_profile: Sequence[ProfilePoint],
    style: GraduationStyle,
    material: bpy.types.Material,
    collection: bpy.types.Collection,
    outer_deformer: RingDeformer | None = None,
) -> bpy.types.Object:
    """Convert text to a mesh and analytically wrap every vertex onto the vessel.

    A rotated flat text object only has the correct facing direction; its glyphs
    remain planar. Here local X is interpreted as arc length, local Y as vertical
    displacement, and local Z as ink thickness. Each vertex is remapped to the
    true outer profile at its own height and angle. This follows tapered walls
    and local angular deformation without depending on Shrinkwrap context or a
    fixed-radius cylindrical Bend modifier.
    """

    obj = _font_curve_to_mesh(name, text, style, collection)
    _subdivide_label_mesh(obj.data, style.text_subdivision_cuts)

    base_theta = math.radians(style.end_angle_deg)
    reference_radius = surface_radius_at(
        outer_profile, z, base_theta, outer_deformer
    )

    minimum_depth = min(vertex.co.z for vertex in obj.data.vertices)

    for vertex in obj.data.vertices:
        local = vertex.co.copy()
        tangent_distance = style.label_tangent_offset + local.x
        theta = base_theta + tangent_distance / reference_radius
        sample_z = z + local.y
        wall_radius, surface_z = surface_point_at(
            outer_profile, sample_z, theta, outer_deformer
        )
        # Normalize the font's extrusion depth so its nearest face, rather
        # than its object origin, receives the requested surface clearance.
        ink_depth = local.z - minimum_depth
        radius = wall_radius + style.text_surface_clearance + ink_depth
        vertex.co = (
            radius * math.cos(theta),
            radius * math.sin(theta),
            surface_z,
        )

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    if bm.faces:
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(obj.data)
    bm.free()

    obj.data.validate(verbose=False)
    obj.data.update()
    obj.data.materials.append(material)
    return obj


def add_volume_graduations(
    inner_profile: Sequence[ProfilePoint],
    outer_profile: Sequence[ProfilePoint],
    nominal_volume_ml: int | float,
    minor_interval_ml: int | float,
    major_interval_ml: int | float,
    style: GraduationStyle,
    material: bpy.types.Material,
    collection: bpy.types.Collection,
    outer_deformer: RingDeformer | None = None,
) -> list[bpy.types.Object]:
    """Add calculated curved ticks and conformal labels to a vessel surface."""

    nominal_volume_ml = _positive_whole_ml(
        nominal_volume_ml,
        name="nominal_volume_ml",
    )
    minor_interval_ml = _positive_whole_ml(
        minor_interval_ml,
        name="minor_interval_ml",
    )
    major_interval_ml = _positive_whole_ml(
        major_interval_ml,
        name="major_interval_ml",
    )

    if major_interval_ml % minor_interval_ml != 0:
        raise ValueError("major_interval_ml must be divisible by minor_interval_ml.")

    created: list[bpy.types.Object] = []
    levels = graduation_levels(
        inner_profile,
        maximum_volume_ml=nominal_volume_ml,
        interval_ml=minor_interval_ml,
        top_clearance=style.top_clearance,
    )

    for value_ml, z in levels:
        is_major = value_ml % major_interval_ml == 0
        created.append(
            create_surface_arc_tick(
                name=f"Tick_{value_ml:03d}mL",
                z=z,
                physical_length=style.major_length if is_major else style.minor_length,
                outer_profile=outer_profile,
                style=style,
                material=material,
                collection=collection,
                outer_deformer=outer_deformer,
            )
        )
        if is_major:
            created.append(
                create_conformal_surface_label(
                    name=f"Label_{value_ml}mL",
                    text=str(value_ml),
                    z=z,
                    outer_profile=outer_profile,
                    style=style,
                    material=material,
                    collection=collection,
                    outer_deformer=outer_deformer,
                )
            )
    return created


def _create_plane_object(
    name: str,
    size: float,
    z: float,
    material: bpy.types.Material,
    collection: bpy.types.Collection,
) -> bpy.types.Object:
    half = size / 2.0
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(
        [(-half, -half, z), (half, -half, z), (half, half, z), (-half, half, z)],
        [],
        [(0, 1, 2, 3)],
    )
    mesh.update(calc_edges=True)
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    mesh.materials.append(material)
    return obj


def create_grid_floor(
    collection: bpy.types.Collection,
    size: float = 0.55,
    z: float = -0.00015,
    spacing: float = 0.010,
    minor_width: float = 0.00038,
    major_width: float = 0.00090,
    major_every: int = 5,
) -> tuple[bpy.types.Object, bpy.types.Object]:
    """Create a real geometric grid instead of a lighting-sensitive texture.

    The base plane and the grid are separate meshes. Grid lines sit a tiny
    distance above the base and use dark materials, so the pattern remains
    visible under strong lighting and gives transparent glass a reliable
    refraction/background cue. Every fifth line is wider and darker.
    """

    if spacing <= 0.0 or major_every <= 0:
        raise ValueError("Grid spacing and major_every must be positive.")

    floor = _create_plane_object(
        "Studio_Floor",
        size,
        z,
        create_floor_material(),
        collection,
    )

    half = size / 2.0
    # Keep the strips safely above the floor to avoid raster depth fighting.
    line_z = z + mm(0.060)
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int, int]] = []
    material_indices: list[int] = []

    def add_strip(x0: float, y0: float, x1: float, y1: float, width: float, index: int) -> None:
        dx = x1 - x0
        dy = y1 - y0
        length = math.hypot(dx, dy)
        nx = -dy / length * width / 2.0
        ny = dx / length * width / 2.0
        start = len(vertices)
        vertices.extend(
            [
                (x0 + nx, y0 + ny, line_z),
                (x1 + nx, y1 + ny, line_z),
                (x1 - nx, y1 - ny, line_z),
                (x0 - nx, y0 - ny, line_z),
            ]
        )
        faces.append((start, start + 1, start + 2, start + 3))
        material_indices.append(index)

    max_index = int(math.floor(half / spacing))
    for grid_index in range(-max_index, max_index + 1):
        coordinate = grid_index * spacing
        is_major = grid_index % major_every == 0
        width = major_width if is_major else minor_width
        material_index = 1 if is_major else 0
        add_strip(coordinate, -half, coordinate, half, width, material_index)
        add_strip(-half, coordinate, half, coordinate, width, material_index)

    mesh = bpy.data.meshes.new("Studio_Grid_Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(create_minor_grid_material())
    mesh.materials.append(create_major_grid_material())
    for polygon, material_index in zip(mesh.polygons, material_indices):
        polygon.material_index = material_index
    mesh.update(calc_edges=True)

    grid = bpy.data.objects.new("Studio_Grid", mesh)
    collection.objects.link(grid)
    return floor, grid


def _enable_eevee_raytracing(scene: bpy.types.Scene) -> None:
    """Enable Blender 5.2 Eevee ray tracing when the active build exposes it."""

    eevee = scene.eevee
    if hasattr(eevee, "use_raytracing"):
        eevee.use_raytracing = True

    if hasattr(eevee, "ray_tracing_method"):
        enum_items = eevee.bl_rna.properties["ray_tracing_method"].enum_items
        identifiers = {item.identifier for item in enum_items}
        if "SCREEN" in identifiers:
            eevee.ray_tracing_method = "SCREEN"

    if hasattr(eevee, "taa_render_samples"):
        eevee.taa_render_samples = 128


def configure_scene(
    resolution: int = 768,
    transparent_background: bool = False,
    render_engine: str = "BLENDER_EEVEE",
) -> bpy.types.Scene:
    """Configure a restrained product-render scene for Blender 5.2.

    Eevee and Cycles need different exposure and world-energy balances. Cycles
    tends to accumulate much more diffuse and refracted light, so it gets a
    dimmer background, a lower exposure, and a slightly softer contrast look.
    """

    if bpy.app.version[:2] != (5, 2):
        raise RuntimeError(
            f"This toolkit targets Blender 5.2 only; current version is "
            f"{bpy.app.version_string}."
        )
    if render_engine not in {"BLENDER_EEVEE", "CYCLES"}:
        raise ValueError("render_engine must be BLENDER_EEVEE or CYCLES.")

    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.length_unit = "MILLIMETERS"
    scene.unit_settings.scale_length = 1.0

    scene.render.engine = render_engine
    scene.render.resolution_x = resolution
    scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "16"
    scene.render.film_transparent = transparent_background

    if render_engine == "BLENDER_EEVEE":
        _enable_eevee_raytracing(scene)
        world_color = (0.72, 0.75, 0.80, 1.0)
        world_strength = 0.20
        exposure = -0.35
        look_name = "AgX - Medium High Contrast"
    else:
        scene.cycles.samples = 192
        scene.cycles.use_denoising = True
        if hasattr(scene.cycles, "caustics_reflective"):
            scene.cycles.caustics_reflective = False
        if hasattr(scene.cycles, "caustics_refractive"):
            scene.cycles.caustics_refractive = False
        world_color = (0.60, 0.62, 0.66, 1.0)
        world_strength = 0.085
        exposure = -0.85
        look_name = "AgX - Medium Contrast"

    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    nodes = world.node_tree.nodes
    links = world.node_tree.links
    nodes.clear()

    background = nodes.new("ShaderNodeBackground")
    background.location = (0, 0)
    background.inputs["Color"].default_value = world_color
    background.inputs["Strength"].default_value = world_strength

    output = nodes.new("ShaderNodeOutputWorld")
    output.location = (320, 0)
    links.new(background.outputs["Background"], output.inputs["Surface"])

    scene.view_settings.view_transform = "AgX"
    scene.view_settings.exposure = exposure
    scene.view_settings.gamma = 1.0
    try:
        scene.view_settings.look = look_name
    except (TypeError, ValueError):
        pass
    return scene


def look_at(
    obj: bpy.types.Object,
    target: Vector,
    track_axis: str = "-Z",
    up_axis: str = "Y",
) -> None:
    direction = target - obj.location
    obj.rotation_euler = direction.to_track_quat(track_axis, up_axis).to_euler()


def create_camera(
    collection: bpy.types.Collection,
    name: str = "Product_Camera",
) -> bpy.types.Object:
    camera_data = bpy.data.cameras.new(name)
    camera_data.sensor_width = 36.0
    camera_data.clip_start = 0.01
    camera_data.clip_end = 10.0
    camera = bpy.data.objects.new(name, camera_data)
    collection.objects.link(camera)
    bpy.context.scene.camera = camera
    return camera


def create_area_light(
    name: str,
    location: Sequence[float],
    energy: float,
    size: float,
    target: Vector,
    collection: bpy.types.Collection,
    shape: str = "RECTANGLE",
    size_y: float | None = None,
) -> bpy.types.Object:
    light_data = bpy.data.lights.new(name=name, type="AREA")
    light_data.energy = energy
    light_data.shape = shape
    light_data.size = size
    if shape == "RECTANGLE" and size_y is not None:
        light_data.size_y = size_y
    if hasattr(light_data, "use_shadow"):
        light_data.use_shadow = True
    if hasattr(light_data, "shadow_soft_size"):
        light_data.shadow_soft_size = max(0.001, min(size, size_y or size) * 0.35)
    light = bpy.data.objects.new(name, light_data)
    collection.objects.link(light)
    light.location = Vector(location)
    look_at(light, target)
    return light


def setup_glass_product_lighting(
    collection: bpy.types.Collection,
    target: Vector,
) -> list[bpy.types.Object]:
    """Use restrained strip-and-fill lighting for transparent laboratory glass.

    The left and right strips remain the main shape-defining sources. Front and
    top fills are intentionally modest so Cycles does not wash the vessel and
    floor toward white. A faint rear fill keeps the rim and wall thickness from
    disappearing when the global exposure is lowered.
    """

    return [
        create_area_light(
            "Left_Strip",
            (0.145, -0.105, 0.145),
            28.0,
            0.032,
            target,
            collection,
            size_y=0.190,
        ),
        create_area_light(
            "Right_Strip",
            (-0.135, 0.020, 0.135),
            34.0,
            0.028,
            target,
            collection,
            size_y=0.175,
        ),
        create_area_light(
            "Front_Fill",
            (-0.040, -0.160, 0.110),
            14.0,
            0.135,
            target,
            collection,
            size_y=0.100,
        ),
        create_area_light(
            "Top_Softbox",
            (0.018, 0.010, 0.270),
            9.0,
            0.120,
            target,
            collection,
            size_y=0.085,
        ),
        create_area_light(
            "Rear_Soft",
            (0.010, 0.145, 0.135),
            6.0,
            0.110,
            target,
            collection,
            size_y=0.085,
        ),
    ]


def orbit_location(target: Vector, view: RenderView) -> Vector:
    azimuth = math.radians(view.azimuth_deg)
    elevation = math.radians(view.elevation_deg)
    horizontal = view.distance * math.cos(elevation)
    return Vector(
        (
            target.x + horizontal * math.cos(azimuth),
            target.y + horizontal * math.sin(azimuth),
            target.z + view.distance * math.sin(elevation),
        )
    )


def render_views(
    camera: bpy.types.Object,
    target: Vector,
    views: Sequence[RenderView],
    output_directory: str | Path,
    filename_prefix: str,
) -> list[Path]:
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    scene.camera = camera

    rendered: list[Path] = []
    for view in views:
        camera.location = orbit_location(target, view)
        camera.data.lens = view.lens_mm
        look_at(camera, target)
        output_path = output_directory / f"{filename_prefix}_{view.name}.png"
        scene.render.filepath = str(output_path)
        bpy.ops.render.render(write_still=True)
        rendered.append(output_path)
        print(f"Rendered: {output_path}")
    return rendered


def save_blend(filepath: str | Path) -> Path:
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    return path
