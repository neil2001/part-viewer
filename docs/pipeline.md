# Pipeline

The pipeline lives in [`pipeline/excavate.py`](../pipeline/excavate.py). It turns the source STEP into excavated CAD, noisy meshes, a deviation field, and indication metadata for the viewer.

## Setup

```bash
cd pipeline
uv venv --python 3.12
uv sync
```

Dependencies (see [`pipeline/pyproject.toml`](../pipeline/pyproject.toml)):

- **cadquery** — STEP I/O and B-rep booleans  
- **numpy / scipy** — sampling, frames, quaternions  
- **trimesh + rtree** — tessellation export, closest-point deviation  

Use Python **3.12**. CadQuery does not ship wheels for 3.14.

## Run

```bash
.venv/bin/python excavate.py
```

From the repo root, the script expects [`impeller.stp`](../impeller.stp) next to `pipeline/`.

## Pipeline stages

```text
impeller.stp
    → load solid (CadQuery)
    → copy → output/original.stp
    → sample sites (flat faces + fins/body)
    → boolean cut sphere-cluster scoops
    → output/excavated.stp
    → tessellate original + excavated
    → Gaussian noise along normals (excavated only)
    → output/original.glb, output/modified.glb
    → closest-point deviation → output/deviation.bin
    → per-site bbox meta → output/meta.json
    → copy GLBs / bin / meta → viewer/public/models/
```

### 1. Site selection

Targets about **30** indications:

| Pool | Quota | How identified |
|------|-------|----------------|
| Flat top / bottom | ~20 (oversampled, then capped) | Planar faces with \|n · shaft_axis\| > 0.85 |
| Fin / body | ~10 | B-spline blades vs cylinders / other body |

- Shaft axis is along **+X** through the STEP revolution axis origin.  
- Sites stay off the bore (`radial > 55 mm`) and at least ~30 mm apart.  
- Scoop sizes vary in three bands (~12–28 mm across, ~1.5–3.5 mm deep), capped by local wall thickness (~40%).  

Kinds written to meta: `flat_top`, `flat_bottom`, `fin`, `body`.

### 2. Excavation (CAD)

Each site is cut with **2–3 overlapping spheres** centered just outside the surface so the bite is a shallow U-dish (grind-out style), not a deep punch-through.

Cuts that create internal voids or remove an unreasonable volume are rejected or repaired (outer shell only). Failed sites are skipped; oversampling on flats aims to still land near the flat quota.

`output/excavated.stp` is the boolean result **without** mesh noise.

### 3. Tessellation and noise

- Linear deflection ≈ **0.35 mm**.  
- On the excavated mesh only: displace vertices along normals with Gaussian σ = **0.05 mm** (seeded).  

### 4. Deviation

For every modified-mesh vertex, unsigned closest-point distance to the original mesh is written as little-endian `float32` in [`output/deviation.bin`](../output/deviation.bin), same order as `modified.glb` positions.

### 5. Indication bounding boxes

For each successful site, meta includes an oriented box:

| Field | Meaning |
|-------|---------|
| `center` | Mid-cavity (`point − n × depth/2`) |
| `length_mm` / `width_mm` | Footprint in the tangent plane (~1.15× diameter with aspect jitter) |
| `depth_mm` | Scoop depth |
| `quaternion` | `[x, y, z, w]` for local frame (length, width, into-material) |

## Outputs

| Path | Description |
|------|-------------|
| `output/original.stp` | Copy of source STEP |
| `output/excavated.stp` | Solid after scoops |
| `output/original.glb` | Tessellated original |
| `output/modified.glb` | Tessellated excavated + noise |
| `output/deviation.bin` | Per-vertex distances (mm) |
| `output/meta.json` | Counts, sites, bboxes |
| `viewer/public/models/*` | Same assets for the Vite app |

## Tuning knobs

Constants near the top of `excavate.py`:

- `TARGET_SITES`, `N_FLAT`, `N_OTHER`  
- `MIN_SPACING_MM`, `BORE_CLEARANCE_MM`  
- `TESS_TOLERANCE`, `NOISE_SIGMA_MM`  
- `SEED` — reproducible site layout  
- `SIZE_BANDS` — diameter / depth ranges  

Change these and re-run `excavate.py`. No viewer rebuild is required beyond a browser refresh once `public/models` is updated.

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| `ModuleNotFoundError: cadquery` | Wrong Python; use `.venv/bin/python` after `uv sync` |
| Few flat indications | Tool/boolean skips; check log for `tool build failed` / void rejects |
| Max deviation ≫ scoop depth | Remeshing / edge cases; use heatmap clamp ~3–4 mm for scoops |
| Viewer shows old geometry | Pipeline did not finish copy, or hard-refresh the browser |
