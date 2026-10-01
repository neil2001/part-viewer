# Impeller weldmap

Simulate casting inclusion grind-outs on an impeller STEP model, compare the original and modified meshes with a distance heatmap, and inspect each indication with oriented bounding boxes in a React Three Fiber viewer.

## What it does

1. **Pipeline** — Loads [`impeller.stp`](impeller.stp), cuts shallow weld-prep scoops (biased to the top/bottom flat faces, plus fins/body), adds light Gaussian surface noise, and exports GLBs plus a per-vertex deviation field.
2. **Viewer** — Overlays the original (ghost) and modified (heatmap) meshes, draws wireframe boxes around each indication, and shows length × width × depth on hover.

Units throughout are **millimeters**. The part is roughly Ø542 mm × 293 mm long.

## Requirements

| Tool | Version / notes |
|------|-----------------|
| [uv](https://docs.astral.sh/uv/) | Python package manager |
| Python | 3.12 (CadQuery wheels; 3.14 is not supported) |
| Node.js | 20+ recommended |
| npm | Comes with Node |

## Quick start

### 1. Generate meshes (pipeline)

```bash
cd pipeline
uv venv --python 3.12
uv sync
.venv/bin/python excavate.py
```

This writes assets under [`output/`](output/) and copies them into [`viewer/public/models/`](viewer/public/models/). A full run typically takes several minutes (STEP load, ~30 boolean cuts, tessellation, proximity).

### 2. Run the viewer

```bash
cd viewer
npm install
npm run dev
```

Open the URL Vite prints (default [http://127.0.0.1:5173](http://127.0.0.1:5173)).

### 3. Use the UI

- **Original (ghost)** — translucent unmodified mesh  
- **Modified heatmap** — excavated + noisy mesh colored by deviation  
- **Indication boxes** — wireframes around each scoop; hover for L × W × D (mm)  
- **Heatmap clamp** — set ~3–4 mm for scoops, ~0.15 mm to see surface noise  

Orbit with the mouse; scroll to zoom.

## Project layout

```text
impeller.stp              Source CAD (Inventor STEP)
pipeline/                 CadQuery / NumPy / Trimesh excavation script
  excavate.py             Main pipeline entrypoint
  pyproject.toml          uv / Python deps
output/                   Generated STEP, GLB, deviation.bin, meta.json
viewer/                   Vite + React + R3F app
  public/models/          Assets served to the browser (copied by pipeline)
docs/
  pipeline.md             Pipeline details
  frontend.md             Viewer details
```

## Docs

- [Pipeline](docs/pipeline.md) — site selection, booleans, exports, regenerating assets  
- [Frontend](docs/frontend.md) — stack, layers, heatmap, indication boxes  

## Regenerate after code changes

```bash
cd pipeline && .venv/bin/python excavate.py
cd ../viewer && npm run dev
```

Hard-refresh the browser if GLBs look cached.
