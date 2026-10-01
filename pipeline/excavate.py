#!/usr/bin/env python3
"""Cut weld-prep excavations into the impeller STEP, add mesh noise, export GLBs + deviation."""

from __future__ import annotations

import math
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import cadquery as cq
import numpy as np
import trimesh
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.GeomAbs import (
    GeomAbs_BSplineSurface,
    GeomAbs_BezierSurface,
    GeomAbs_Cylinder,
    GeomAbs_Plane,
    GeomAbs_SurfaceOfRevolution,
    GeomAbs_Torus,
)
from OCP.gp import gp_Pnt
from OCP.TopAbs import TopAbs_IN, TopAbs_ON

ROOT = Path(__file__).resolve().parents[1]
STEP_SRC = ROOT / "impeller.stp"
OUT = ROOT / "output"
VIEWER_MODELS = ROOT / "viewer" / "public" / "models"

SEED = 42
TARGET_SITES = 30
N_FLAT = 20  # ~10 top + ~10 bottom axial flats
N_OTHER = 10  # fins / body
MIN_SPACING_MM = 30.0
BORE_CLEARANCE_MM = 55.0  # keep away from hub bore (~38.5 mm radius)
MAX_SCOOP_DEPTH_MM = 5.0
# Isotropic surface mesh: equal min/max edge length so triangles stay
# near-equilateral instead of stretching along the CAD surface.
TESS_EDGE_MM = 2.5
# Chordal error cap used only if the isotropic mesher falls back to OCCT.
TESS_TOLERANCE = 0.35
NOISE_SIGMA_MM = 0.05
FLAT_NORMAL_DOT = 0.85  # |n · axis| above this ⇒ axial flat face

# Axis of the impeller (from STEP SURFACE_OF_REVOLUTION)
AXIS_ORIGIN = np.array([118.85551844828268, 499.27337266807967, 0.0])
AXIS_DIR = np.array([1.0, 0.0, 0.0])

SIZE_BANDS = [
    # (label, diameter_range_mm, depth_range_mm, weight)
    ("small", (12.0, 16.0), (2.0, 3.2), 0.30),
    ("medium", (16.0, 22.0), (3.2, 4.2), 0.40),
    ("large", (22.0, 28.0), (4.2, MAX_SCOOP_DEPTH_MM), 0.30),
]


@dataclass
class Site:
    point: np.ndarray
    normal: np.ndarray
    kind: str  # "flat_top" | "flat_bottom" | "fin" | "body"
    diameter: float
    depth: float
    band: str
    # Optional footprint aspect for bbox (set at pick / tool time)
    length_mm: float = 0.0
    width_mm: float = 0.0


def axis_frame(p: np.ndarray) -> tuple[float, float, float]:
    """Return (axial, radial, angle) relative to the shaft axis."""
    rel = p - AXIS_ORIGIN
    axial = float(np.dot(rel, AXIS_DIR))
    radial_vec = rel - axial * AXIS_DIR
    radial = float(np.linalg.norm(radial_vec))
    # Angle in YZ plane around X
    angle = math.atan2(rel[2], rel[1] - 0.0)  # relative to AXIS_ORIGIN y already subtracted via rel
    # Use radial_vec for a stable angle
    if radial > 1e-9:
        angle = math.atan2(radial_vec[2], radial_vec[1])
    return axial, radial, angle


def face_surface_type(face: cq.Face) -> int:
    adaptor = BRepAdaptor_Surface(face.wrapped)
    return adaptor.GetType()


def is_fin_face(face: cq.Face) -> bool:
    t = face_surface_type(face)
    return t in (GeomAbs_BSplineSurface, GeomAbs_BezierSurface)


def is_body_face(face: cq.Face) -> bool:
    t = face_surface_type(face)
    return t in (
        GeomAbs_Cylinder,
        GeomAbs_Plane,
        GeomAbs_Torus,
        GeomAbs_SurfaceOfRevolution,
    )


def flat_face_kind(face: cq.Face, solid: cq.Solid) -> str | None:
    """Return 'flat_top' / 'flat_bottom' if face is an axial planar end, else None."""
    if face_surface_type(face) != GeomAbs_Plane:
        return None
    try:
        center = face.Center()
        nrm = face.normalAt(center)
    except Exception:
        return None
    nm = np.array([nrm.x, nrm.y, nrm.z], dtype=float)
    ln = np.linalg.norm(nm)
    if ln < 1e-9:
        return None
    nm /= ln
    pt = np.array([center.x, center.y, center.z], dtype=float)
    nm = outward_normal(solid, pt, nm)
    axial_dot = float(np.dot(nm, AXIS_DIR))
    if abs(axial_dot) < FLAT_NORMAL_DOT:
        return None
    # +X outward ⇒ top (larger axial end); −X ⇒ bottom
    return "flat_top" if axial_dot > 0 else "flat_bottom"


def tangent_frame(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = normal / np.linalg.norm(normal)
    tmp = np.array([1.0, 0.0, 0.0]) if abs(n[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    t1 = np.cross(n, tmp)
    t1 /= np.linalg.norm(t1)
    t2 = np.cross(n, t1)
    return t1, t2


def basis_to_quaternion(t1: np.ndarray, t2: np.ndarray, n_in: np.ndarray) -> list[float]:
    """Quaternion [x,y,z,w] for rotation matrix with columns (t1, t2, n_in)."""
    from scipy.spatial.transform import Rotation

    R = np.column_stack([t1, t2, n_in])
    # Ensure right-handed orthonormal
    if np.linalg.det(R) < 0:
        t2 = -t2
        R = np.column_stack([t1, t2, n_in])
    quat = Rotation.from_matrix(R).as_quat()  # x, y, z, w
    return [float(quat[0]), float(quat[1]), float(quat[2]), float(quat[3])]


def site_bbox(site: Site) -> dict:
    """Oriented box enclosing the scoop cavity."""
    n = site.normal / np.linalg.norm(site.normal)
    t1, t2 = tangent_frame(n)
    length = site.length_mm if site.length_mm > 0 else site.diameter * 1.15
    width = site.width_mm if site.width_mm > 0 else site.diameter * 1.05
    depth = site.depth
    # Into the material
    center = site.point - n * (depth / 2.0)
    n_in = -n
    return {
        "center": [round(float(x), 3) for x in center],
        "length_mm": round(float(length), 2),
        "width_mm": round(float(width), 2),
        "depth_mm": round(float(depth), 3),
        "quaternion": basis_to_quaternion(t1, t2, n_in),
    }


def outward_normal(solid: cq.Solid, point: np.ndarray, normal: np.ndarray) -> np.ndarray:
    """Flip normal if it points into the solid."""
    classifier = BRepClass3d_SolidClassifier(solid.wrapped)
    probe = point + normal * 0.5
    classifier.Perform(gp_Pnt(float(probe[0]), float(probe[1]), float(probe[2])), 1e-4)
    if classifier.State() == TopAbs_IN:
        return -normal
    return normal


def sample_face_candidates(
    face: cq.Face,
    solid: cq.Solid,
    rng: np.random.Generator,
    n: int,
    kind: str,
) -> list[tuple[np.ndarray, np.ndarray, str]]:
    """Sample (point, outward_normal, kind) on a face via UV grid with jitter."""
    try:
        u0, u1, v0, v1 = face.uvBounds()
    except Exception:
        return []
    if not math.isfinite(u0 + u1 + v0 + v1) or abs(u1 - u0) < 1e-12 or abs(v1 - v0) < 1e-12:
        return []

    surf = BRep_Tool.Surface_s(face.wrapped)
    out: list[tuple[np.ndarray, np.ndarray, str]] = []
    # Grid density scales with area
    area = max(face.Area(), 1.0)
    n = max(n, int(math.sqrt(area) / 8.0))
    nu = max(3, int(math.sqrt(n)))
    nv = max(3, int(math.ceil(n / nu)))

    for iu in range(nu):
        for iv in range(nv):
            fu = (iu + 0.5 + rng.uniform(-0.35, 0.35)) / nu
            fv = (iv + 0.5 + rng.uniform(-0.35, 0.35)) / nv
            fu = min(max(fu, 0.05), 0.95)
            fv = min(max(fv, 0.05), 0.95)
            u = u0 + fu * (u1 - u0)
            v = v0 + fv * (v1 - v0)
            try:
                p = surf.Value(u, v)
                vec = cq.Vector(p.X(), p.Y(), p.Z())
                nrm = face.normalAt(vec)
            except Exception:
                continue
            pt = np.array([p.X(), p.Y(), p.Z()], dtype=float)
            nm = np.array([nrm.x, nrm.y, nrm.z], dtype=float)
            ln = np.linalg.norm(nm)
            if ln < 1e-9:
                continue
            nm /= ln
            nm = outward_normal(solid, pt, nm)
            _, radial, _ = axis_frame(pt)
            if radial < BORE_CLEARANCE_MM:
                continue
            out.append((pt, nm, kind))
    return out


def pick_size(rng: np.random.Generator) -> tuple[str, float, float]:
    weights = np.array([b[3] for b in SIZE_BANDS], dtype=float)
    weights /= weights.sum()
    idx = int(rng.choice(len(SIZE_BANDS), p=weights))
    label, (d0, d1), (z0, z1), _ = SIZE_BANDS[idx]
    diameter = float(rng.uniform(d0, d1))
    depth = float(rng.uniform(z0, z1))
    return label, diameter, depth


def local_thickness(solid: cq.Solid, point: np.ndarray, normal: np.ndarray) -> float:
    """Estimate wall thickness by probing inward along -normal."""
    classifier = BRepClass3d_SolidClassifier(solid.wrapped)
    # Walk inward until we leave the solid
    step = 0.4
    max_t = 40.0
    t = step
    last_in = True
    while t <= max_t:
        q = point - normal * t
        classifier.Perform(gp_Pnt(float(q[0]), float(q[1]), float(q[2])), 1e-4)
        state = classifier.State()
        inside = state in (TopAbs_IN, TopAbs_ON)
        if last_in and not inside:
            return t
        last_in = inside
        t += step
    return max_t if last_in else t


def select_sites(
    solid: cq.Solid,
    rng: np.random.Generator,
    target: int = TARGET_SITES,
) -> list[Site]:
    faces = solid.Faces()
    flat_top_faces: list[cq.Face] = []
    flat_bot_faces: list[cq.Face] = []
    fin_faces: list[cq.Face] = []
    body_faces: list[cq.Face] = []

    for f in faces:
        fk = flat_face_kind(f, solid)
        if fk == "flat_top":
            flat_top_faces.append(f)
        elif fk == "flat_bottom":
            flat_bot_faces.append(f)
        elif is_fin_face(f):
            fin_faces.append(f)
        elif is_body_face(f):
            body_faces.append(f)

    print(
        f"  faces: {len(faces)} total, "
        f"{len(flat_top_faces)} flat_top, {len(flat_bot_faces)} flat_bottom, "
        f"{len(fin_faces)} fin, {len(body_faces)} body"
    )

    flat_top_cands: list[tuple[np.ndarray, np.ndarray, str]] = []
    flat_bot_cands: list[tuple[np.ndarray, np.ndarray, str]] = []
    fin_cands: list[tuple[np.ndarray, np.ndarray, str]] = []
    body_cands: list[tuple[np.ndarray, np.ndarray, str]] = []

    for f in flat_top_faces:
        flat_top_cands.extend(sample_face_candidates(f, solid, rng, n=40, kind="flat_top"))
    for f in flat_bot_faces:
        flat_bot_cands.extend(sample_face_candidates(f, solid, rng, n=40, kind="flat_bottom"))
    for f in fin_faces:
        fin_cands.extend(sample_face_candidates(f, solid, rng, n=20, kind="fin"))
    for f in body_faces:
        body_cands.extend(sample_face_candidates(f, solid, rng, n=14, kind="body"))

    print(
        f"  candidates: {len(flat_top_cands)} flat_top, {len(flat_bot_cands)} flat_bottom, "
        f"{len(fin_cands)} fin, {len(body_cands)} body"
    )

    n_flat = min(N_FLAT + 6, 28)  # oversample flats; cuts may skip a few
    n_other = min(N_OTHER + 2, 14)
    n_top = n_flat // 2
    n_bot = n_flat - n_top
    n_fin = n_other // 2
    n_body = n_other - n_fin

    def greedy_pick(
        cands: list[tuple[np.ndarray, np.ndarray, str]],
        n: int,
        selected: list[Site],
    ) -> list[Site]:
        if not cands or n <= 0:
            return []
        order = rng.permutation(len(cands))
        picked: list[Site] = []
        angle_bins = np.zeros(12, dtype=int)

        for oi in order:
            if len(picked) >= n:
                break
            pt, nm, kind = cands[int(oi)]
            ok = True
            for s in selected + picked:
                if np.linalg.norm(pt - s.point) < MIN_SPACING_MM:
                    ok = False
                    break
            if not ok:
                continue

            _, _, ang = axis_frame(pt)
            bin_i = int((ang + math.pi) / (2 * math.pi) * 12) % 12
            if angle_bins[bin_i] >= max(2, n // 4 + 1) and rng.random() < 0.7:
                continue

            thickness = local_thickness(solid, pt, nm)
            if thickness < 2.5:
                continue
            band, diameter, depth = pick_size(rng)
            # Allow scoops up to 5 mm, but stay inside about half the local wall.
            max_depth = min(MAX_SCOOP_DEPTH_MM, max(1.5, 0.5 * thickness))
            depth = min(depth, max_depth)
            if thickness < 8.0:
                diameter = min(diameter, 18.0)

            # Footprint for bbox: slightly elongated irregular scoop
            aspect = float(rng.uniform(0.88, 1.12))
            length = diameter * 1.15
            width = diameter * 1.05 * aspect

            picked.append(
                Site(
                    point=pt,
                    normal=nm,
                    kind=kind,
                    diameter=diameter,
                    depth=depth,
                    band=band,
                    length_mm=length,
                    width_mm=width,
                )
            )
            angle_bins[bin_i] += 1
        return picked

    selected: list[Site] = []
    selected.extend(greedy_pick(flat_top_cands, n_top, selected))
    selected.extend(greedy_pick(flat_bot_cands, n_bot, selected))
    # Top up flat quota from the other flat end if one side was short
    flat_have = sum(1 for s in selected if s.kind.startswith("flat_"))
    if flat_have < n_flat:
        remaining_flat = n_flat - flat_have
        selected.extend(
            greedy_pick(flat_top_cands + flat_bot_cands, remaining_flat, selected)
        )

    selected.extend(greedy_pick(fin_cands, n_fin, selected))
    selected.extend(greedy_pick(body_cands, n_body, selected))

    if len(selected) < target:
        remaining = target - len(selected)
        pool = flat_top_cands + flat_bot_cands + fin_cands + body_cands
        selected.extend(greedy_pick(pool, remaining, selected))

    return selected


def make_scoop_tool(site: Site, rng: np.random.Generator, solid: cq.Solid | None = None) -> cq.Solid | None:
    """Build an irregular scoop from 2–3 overlapping spheres centered outside the surface."""
    R = site.diameter / 2.0
    depth = site.depth
    # Keep sphere radius large enough relative to depth for a shallow dish,
    # but not so large that it risks clipping distant geometry.
    sphere_r = max(R * 1.05, depth * 4.0)
    sphere_r = min(sphere_r, R * 1.4)

    n_spheres = int(rng.integers(2, 4))
    tools: list[cq.Solid] = []

    # Orthonormal tangent frame
    n = site.normal
    tmp = np.array([1.0, 0.0, 0.0]) if abs(n[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    t1 = np.cross(n, tmp)
    t1 /= np.linalg.norm(t1)
    t2 = np.cross(n, t1)

    classifier = None
    if solid is not None:
        classifier = BRepClass3d_SolidClassifier(solid.wrapped)

    for i in range(n_spheres):
        # Lateral offset within the scoop footprint
        if i == 0:
            offset_t = np.zeros(3)
            r_i = sphere_r
            d_i = depth
        else:
            ang = rng.uniform(0, 2 * math.pi)
            rad = rng.uniform(0.15, 0.4) * R
            offset_t = math.cos(ang) * t1 * rad + math.sin(ang) * t2 * rad
            r_i = sphere_r * rng.uniform(0.8, 1.05)
            d_i = depth * rng.uniform(0.75, 1.0)

        # Center outside the material so the bite depth is ~d_i
        center = site.point + offset_t + n * (r_i - d_i)
        if classifier is not None:
            classifier.Perform(
                gp_Pnt(float(center[0]), float(center[1]), float(center[2])), 1e-4
            )
            if classifier.State() == TopAbs_IN:
                # Push outward until outside (or give up after a few steps)
                for push in (0.5, 1.5, 3.0, 6.0, 12.0):
                    center = site.point + offset_t + n * (r_i - d_i + push)
                    classifier.Perform(
                        gp_Pnt(float(center[0]), float(center[1]), float(center[2])),
                        1e-4,
                    )
                    if classifier.State() != TopAbs_IN:
                        break
                else:
                    # Last resort: far outside along normal
                    center = site.point + offset_t + n * (r_i + 8.0)

        try:
            sph = cq.Solid.makeSphere(
                r_i,
                pnt=cq.Vector(float(center[0]), float(center[1]), float(center[2])),
            )
            tools.append(sph)
        except Exception:
            continue

    if not tools:
        return None
    tool = tools[0]
    for extra in tools[1:]:
        try:
            tool = tool.fuse(extra)
        except Exception:
            continue
    return tool


def shell_count(solid: cq.Solid) -> int:
    return len(solid.Shells())


def outer_solid(solid: cq.Solid) -> cq.Solid:
    """Keep only the outer shell so internal voids from bad cuts are discarded."""
    shells = solid.Shells()
    if len(shells) <= 1:
        return solid
    outer = max(shells, key=lambda s: abs(s.Area()))
    try:
        return cq.Solid.makeSolid(outer)
    except Exception:
        # Fallback: sew via Shape
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid

        mk = BRepBuilderAPI_MakeSolid(outer.wrapped)
        mk.Build()
        return cq.Solid(mk.Solid())


def cut_scoops(solid: cq.Solid, sites: list[Site], rng: np.random.Generator) -> tuple[cq.Solid, list[Site]]:
    work = solid
    kept: list[Site] = []
    base_shells = shell_count(work)
    flat_kept = 0
    other_kept = 0

    for i, site in enumerate(sites):
        is_flat = site.kind.startswith("flat_")
        # Stop accepting more once quotas are filled (still try earlier sites)
        if is_flat and flat_kept >= N_FLAT:
            continue
        if not is_flat and other_kept >= N_OTHER:
            continue

        tool = make_scoop_tool(site, rng, solid=work)
        if tool is None:
            print(f"  site {i}: tool build failed, skip")
            continue

        # Ensure outward normal: probe just outside the surface
        probe = site.point + site.normal * 0.5
        classifier = BRepClass3d_SolidClassifier(work.wrapped)
        classifier.Perform(gp_Pnt(float(probe[0]), float(probe[1]), float(probe[2])), 1e-4)
        if classifier.State() == TopAbs_IN:
            site = Site(
                point=site.point,
                normal=-site.normal,
                kind=site.kind,
                diameter=site.diameter,
                depth=site.depth,
                band=site.band,
                length_mm=site.length_mm,
                width_mm=site.width_mm,
            )
            tool = make_scoop_tool(site, rng, solid=work)
            if tool is None:
                print(f"  site {i}: flipped tool build failed, skip")
                continue

        success = False
        for attempt, scale in enumerate((1.0, 0.75)):
            try:
                trial_site = site
                trial_tool = tool
                if scale != 1.0:
                    trial_site = Site(
                        point=site.point,
                        normal=site.normal,
                        kind=site.kind,
                        diameter=site.diameter * scale,
                        depth=site.depth * scale,
                        band=site.band,
                        length_mm=site.length_mm * scale,
                        width_mm=site.width_mm * scale,
                    )
                    trial_tool = make_scoop_tool(trial_site, rng, solid=work)
                    if trial_tool is None:
                        continue
                cut = work.cut(trial_tool)
                if isinstance(cut, cq.Compound):
                    solids = cut.Solids()
                    if not solids:
                        raise RuntimeError("empty compound")
                    cut = max(solids, key=lambda s: s.Volume())
                if not isinstance(cut, cq.Solid):
                    if hasattr(cut, "Solids"):
                        solids = cut.Solids()
                        cut = max(solids, key=lambda s: s.Volume())
                # Reject cuts that create internal voids
                if shell_count(cut) > base_shells:
                    cut = outer_solid(cut)
                if shell_count(cut) > base_shells:
                    raise RuntimeError(f"void created ({shell_count(cut)} shells)")
                # Reject cuts that remove too much volume (> ~50k mm³ for one scoop)
                removed = work.Volume() - cut.Volume()
                if removed > 50_000:
                    raise RuntimeError(f"removed too much volume ({removed:.0f} mm³)")
                work = cut
                site = trial_site
                success = True
                break
            except Exception as exc:
                print(f"  site {i}: cut attempt {attempt} failed: {exc}")
                continue

        if success:
            kept.append(site)
            if is_flat:
                flat_kept += 1
            else:
                other_kept += 1
            print(
                f"  site {i:02d} [{site.kind}/{site.band}] "
                f"d={site.diameter:.1f}mm depth={site.depth:.2f}mm OK"
            )
        else:
            print(f"  site {i:02d}: skipped after retries")

    work = outer_solid(work)
    return work, kept


def _set_gmsh_option(gmsh, name: str, value: float) -> None:
    try:
        gmsh.option.setNumber(name, value)
    except Exception:
        pass


def _mesh_from_triangles(vertices: np.ndarray, faces: np.ndarray) -> trimesh.Trimesh:
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    mesh.merge_vertices()
    mesh.update_faces(mesh.unique_faces())
    mesh.remove_unreferenced_vertices()
    mesh.fix_normals()
    return mesh


def _log_mesh_isotropy(mesh: trimesh.Trimesh, label: str) -> None:
    """Print edge-length spread and triangle aspect ratio (1 = equilateral)."""
    if len(mesh.faces) == 0:
        print(f"  {label}: empty mesh")
        return
    edges = mesh.edges_unique_length
    tri = mesh.triangles
    lengths = np.linalg.norm(
        np.stack(
            [
                tri[:, 0] - tri[:, 1],
                tri[:, 1] - tri[:, 2],
                tri[:, 2] - tri[:, 0],
            ],
            axis=1,
        ),
        axis=2,
    )
    aspect = lengths.max(axis=1) / np.maximum(lengths.min(axis=1), 1e-9)
    print(
        f"  {label}: {len(mesh.vertices)} verts, {len(mesh.faces)} faces; "
        f"edge mm p10/p50/p90 "
        f"{np.percentile(edges, 10):.2f}/"
        f"{np.percentile(edges, 50):.2f}/"
        f"{np.percentile(edges, 90):.2f}; "
        f"aspect p50/p90 {np.median(aspect):.2f}/{np.percentile(aspect, 90):.2f}"
    )


def isotropic_mesh_step(step_path: Path, edge_mm: float = TESS_EDGE_MM) -> trimesh.Trimesh:
    """Frontal-Delaunay surface mesh with a uniform target edge length.

    Min and max size are the same, and curvature-based sizing is off, so the
    tessellation is isotropic. Call this before any vertex noise.
    """
    import gmsh

    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.logger.start()
        # Uniform size in every direction, then Frontal-Delaunay (algo 6).
        for key in (
            "Mesh.MeshSizeMin",
            "Mesh.MeshSizeMax",
            "Mesh.CharacteristicLengthMin",
            "Mesh.CharacteristicLengthMax",
        ):
            _set_gmsh_option(gmsh, key, edge_mm)
        _set_gmsh_option(gmsh, "Mesh.Algorithm", 6)
        _set_gmsh_option(gmsh, "Mesh.MeshSizeFromCurvature", 0)
        _set_gmsh_option(gmsh, "Mesh.MeshSizeFromPoints", 0)
        _set_gmsh_option(gmsh, "Mesh.MeshSizeExtendFromBoundary", 0)
        _set_gmsh_option(gmsh, "Mesh.CharacteristicLengthFromCurvature", 0)
        _set_gmsh_option(gmsh, "Mesh.CharacteristicLengthExtendFromBoundary", 0)
        _set_gmsh_option(gmsh, "Mesh.Smoothing", 10)

        gmsh.model.add("impeller")
        gmsh.model.occ.importShapes(str(step_path))
        gmsh.model.occ.removeAllDuplicates()
        gmsh.model.occ.synchronize()
        # Constant background field so interior edges match the boundary size.
        field = gmsh.model.mesh.field.add("MathEval")
        gmsh.model.mesh.field.setString(field, "F", f"{edge_mm}")
        gmsh.model.mesh.field.setAsBackgroundMesh(field)
        point_entities = gmsh.model.getEntities(0)
        if point_entities:
            gmsh.model.mesh.setSize(point_entities, edge_mm)

        gmsh.model.mesh.generate(2)
        node_tags, coords, _ = gmsh.model.mesh.getNodes()
        if len(node_tags) == 0:
            log = "\n".join(gmsh.logger.get()[-30:])
            raise RuntimeError(f"gmsh returned no nodes\n{log}")
        vertices = np.array(coords, dtype=np.float64).reshape(-1, 3)
        tag_to_i = {int(tag): i for i, tag in enumerate(node_tags)}

        elem_types, _, elem_nodes = gmsh.model.mesh.getElements(dim=2)
        face_blocks: list[np.ndarray] = []
        for etype, nodes in zip(elem_types, elem_nodes):
            nodes_arr = np.array(nodes, dtype=np.int64)
            if int(etype) == 2:
                mapped = np.array(
                    [tag_to_i[int(t)] for t in nodes_arr], dtype=np.int64
                ).reshape(-1, 3)
                face_blocks.append(mapped)
            elif int(etype) == 3:
                quads = np.array(
                    [tag_to_i[int(t)] for t in nodes_arr], dtype=np.int64
                ).reshape(-1, 4)
                face_blocks.append(quads[:, [0, 1, 2]])
                face_blocks.append(quads[:, [0, 2, 3]])
        if not face_blocks:
            log = "\n".join(gmsh.logger.get()[-30:])
            raise RuntimeError(f"gmsh returned no surface elements\n{log}")
        faces = np.vstack(face_blocks)
    finally:
        gmsh.finalize()

    return _mesh_from_triangles(vertices, faces)


def solid_to_trimesh(solid: cq.Solid, edge_mm: float = TESS_EDGE_MM) -> trimesh.Trimesh:
    """Isotropic tessellation of a solid. Does not apply noise."""
    with tempfile.TemporaryDirectory() as tmp:
        step_path = Path(tmp) / "solid.step"
        export_step(solid, step_path)
        try:
            return isotropic_mesh_step(step_path, edge_mm)
        except Exception as exc:
            print(f"  isotropic gmsh mesh failed ({exc}); falling back to OCCT deflection")
    return _occt_deflection_mesh(solid, TESS_TOLERANCE)


def _occt_deflection_mesh(solid: cq.Solid, tolerance: float) -> trimesh.Trimesh:
    verts, faces = solid.tessellate(tolerance)
    v = np.array([[p.x, p.y, p.z] for p in verts], dtype=np.float64)
    f = np.array(faces, dtype=np.int64)
    return _mesh_from_triangles(v, f)


def add_normal_noise(mesh: trimesh.Trimesh, sigma: float, rng: np.random.Generator) -> trimesh.Trimesh:
    mesh = mesh.copy()
    mesh.rezero = False  # type: ignore[attr-defined]
    normals = mesh.vertex_normals
    noise = rng.normal(0.0, sigma, size=len(mesh.vertices))
    mesh.vertices = mesh.vertices + normals * noise[:, None]
    return mesh


def export_glb(mesh: trimesh.Trimesh, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(path, file_type="glb")


def compute_deviation(modified: trimesh.Trimesh, original: trimesh.Trimesh) -> np.ndarray:
    """Unsigned closest-point distance from each modified vertex to the original mesh."""
    _, distance, _ = trimesh.proximity.closest_point(original, modified.vertices)
    return distance.astype(np.float32)


def write_deviation_bin(path: Path, deviation: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    deviation.astype("<f4").tofile(path)


def export_step(solid: cq.Solid, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cq.exporters.export(solid, str(path), exportType="STEP")


def main() -> None:
    rng = np.random.default_rng(SEED)
    OUT.mkdir(parents=True, exist_ok=True)

    print("Loading STEP…")
    result = cq.importers.importStep(str(STEP_SRC))
    solid = result.val()
    if isinstance(solid, cq.Compound):
        solids = solid.Solids()
        solid = max(solids, key=lambda s: s.Volume())
    assert isinstance(solid, cq.Solid)

    bb = solid.BoundingBox()
    print(
        f"Solid bbox: "
        f"x[{bb.xmin:.1f},{bb.xmax:.1f}] "
        f"y[{bb.ymin:.1f},{bb.ymax:.1f}] "
        f"z[{bb.zmin:.1f},{bb.zmax:.1f}]"
    )

    # 1. Untouched copy
    shutil.copy2(STEP_SRC, OUT / "original.stp")
    print(f"Wrote {OUT / 'original.stp'}")

    # 2–3. Sites + cuts
    print("Selecting excavation sites…")
    sites = select_sites(solid, rng, TARGET_SITES)
    n_flat = sum(1 for s in sites if s.kind.startswith("flat_"))
    n_fin = sum(1 for s in sites if s.kind == "fin")
    n_body = sum(1 for s in sites if s.kind == "body")
    print(
        f"Selected {len(sites)} sites "
        f"({n_flat} flat, {n_fin} fin, {n_body} body)"
    )

    print("Cutting scoops…")
    excavated, kept = cut_scoops(solid, sites, rng)
    print(f"Successful excavations: {len(kept)}")
    n_flat_k = sum(1 for s in kept if s.kind.startswith("flat_"))
    print(
        f"  kept: {n_flat_k} flat, "
        f"{sum(1 for s in kept if s.kind == 'fin')} fin, "
        f"{sum(1 for s in kept if s.kind == 'body')} body"
    )
    if len(kept) < 20:
        print("WARNING: fewer than 20 excavations landed; consider re-running with a different seed.")

    export_step(excavated, OUT / "excavated.stp")
    print(f"Wrote {OUT / 'excavated.stp'}")

    # 4. Isotropic tessellation of the CAD surfaces. Noise comes after this.
    print(f"Tessellating isotropically (edge={TESS_EDGE_MM} mm)…")

    def _tessellate(step_path: Path, solid_fallback: cq.Solid, label: str) -> trimesh.Trimesh:
        try:
            mesh = isotropic_mesh_step(step_path, TESS_EDGE_MM)
        except Exception as exc:
            print(f"  {label}: isotropic gmsh mesh failed ({exc}); using OCCT deflection")
            mesh = _occt_deflection_mesh(solid_fallback, TESS_TOLERANCE)
        _log_mesh_isotropy(mesh, label)
        return mesh

    mesh_orig = _tessellate(OUT / "original.stp", solid, "original")
    mesh_exc = _tessellate(OUT / "excavated.stp", excavated, "excavated")

    # 5. Gaussian noise only on the already-tessellated excavated mesh.
    print(f"Adding Gaussian normal noise σ={NOISE_SIGMA_MM} mm…")
    mesh_mod = add_normal_noise(mesh_exc, NOISE_SIGMA_MM, rng)

    export_glb(mesh_orig, OUT / "original.glb")
    export_glb(mesh_mod, OUT / "modified.glb")
    print(f"Wrote {OUT / 'original.glb'} and {OUT / 'modified.glb'}")

    # 6. Deviation
    print("Computing deviation field…")
    deviation = compute_deviation(mesh_mod, mesh_orig)
    write_deviation_bin(OUT / "deviation.bin", deviation)
    print(
        f"Deviation: min={deviation.min():.4f} max={deviation.max():.4f} "
        f"mean={deviation.mean():.4f} mm  ({len(deviation)} values)"
    )

    # Meta for the viewer
    meta = {
        "vertex_count": int(len(deviation)),
        "max_deviation_mm": float(deviation.max()),
        "mean_deviation_mm": float(deviation.mean()),
        "excavation_count": len(kept),
        "noise_sigma_mm": NOISE_SIGMA_MM,
        "tessellation": "isotropic",
        "tess_edge_mm": TESS_EDGE_MM,
        "max_scoop_depth_mm": MAX_SCOOP_DEPTH_MM,
        "units": "mm",
        "sites": [
            {
                "kind": s.kind,
                "band": s.band,
                "diameter_mm": round(s.diameter, 2),
                "depth_mm": round(s.depth, 3),
                "point": [round(float(x), 3) for x in s.point],
                "normal": [round(float(x), 5) for x in s.normal],
                "bbox": site_bbox(s),
            }
            for s in kept
        ],
    }
    import json

    (OUT / "meta.json").write_text(json.dumps(meta, indent=2))
    print(f"Wrote {OUT / 'meta.json'}")

    # Copy into viewer public folder if it exists (or create it)
    VIEWER_MODELS.mkdir(parents=True, exist_ok=True)
    for name in ("original.glb", "modified.glb", "deviation.bin", "meta.json"):
        shutil.copy2(OUT / name, VIEWER_MODELS / name)
    print(f"Copied assets to {VIEWER_MODELS}")
    print("Done.")


if __name__ == "__main__":
    main()
