# Frontend

The viewer is a Vite + React + TypeScript app using React Three Fiber and drei. Source is under [`viewer/`](../viewer/).

## Setup

```bash
cd viewer
npm install
npm run dev
```

Open the local URL (default `http://127.0.0.1:5173`).

Production build:

```bash
npm run build
npm run preview
```

## Stack

| Package | Role |
|---------|------|
| `vite` / `@vitejs/plugin-react` | Dev server and bundler |
| `react` 19 | UI shell (panel, toggles, legend) |
| `@react-three/fiber` | Three.js in React |
| `@react-three/drei` | `Bounds`, `OrbitControls`, `Environment`, `Html`, `useGLTF` |
| `three` | Geometry, materials, colors |

## Assets

The app loads static files from [`viewer/public/models/`](../viewer/public/models/):

| File | Use |
|------|-----|
| `original.glb` | Ghost mesh |
| `modified.glb` | Heatmap mesh |
| `deviation.bin` | `Float32Array` of distances (mm), one per modified vertex |
| `meta.json` | Excavation count, sites, oriented bboxes |

These are produced by the [pipeline](pipeline.md). Re-run `excavate.py` after CAD changes; the script copies into `public/models/`.

## App structure

| File | Responsibility |
|------|----------------|
| [`src/App.tsx`](../viewer/src/App.tsx) | Layout, layer toggles, clamp slider, Canvas / lights / orbit |
| [`src/OverlayScene.tsx`](../viewer/src/OverlayScene.tsx) | Load GLBs, center meshes, paint heatmap colors, host boxes |
| [`src/IndicationBoxes.tsx`](../viewer/src/IndicationBoxes.tsx) | Wireframe OBB per site + hover label |
| [`src/types.ts`](../viewer/src/types.ts) | `Meta`, `Site`, `SiteBBox` |
| [`src/App.css`](../viewer/src/App.css) | Dark inspection chrome |

The impeller STEP axis is **X**. The scene wraps content in `rotation={[0, π/2, 0]}` so the default view looks into the bore. Meshes and boxes share the same centering offset so they stay aligned.

## Layers

| Control | Behavior |
|---------|----------|
| Original (ghost) | Translucent metal; `polygonOffset` to reduce z-fight |
| Modified heatmap | Opaque mesh with vertex colors from deviation |
| Indication boxes | Orange wireframes; invisible hit meshes for hover |

### Heatmap

Deviation `d` is mapped with clamp `C` (slider, mm):

```text
t = clamp(d / C, 0, 1)
color = blue → yellow → red by t
```

- Clamp ≈ **3.5 mm** — scoop cavities dominate  
- Clamp ≈ **0.15 mm** — σ = 0.05 mm noise is visible as speckle  

### Indication boxes

Each `meta.sites[]` entry with a `bbox` renders:

- `BoxGeometry(length, width, depth)` edges in local quaternion frame  
- Transparent mesh for pointer events  
- On hover: drei `Html` label with kind/band and **L × W × D** in mm  

Toggle **Indication boxes** in the side panel to hide all boxes.

## Types (`meta.json` sites)

```ts
type SiteBBox = {
  center: [number, number, number]
  length_mm: number
  width_mm: number
  depth_mm: number
  quaternion: [number, number, number, number] // x, y, z, w
}

type Site = {
  kind: string      // flat_top | flat_bottom | fin | body
  band: string      // small | medium | large
  diameter_mm: number
  depth_mm: number
  point: [number, number, number]
  normal?: [number, number, number]
  bbox: SiteBBox
}
```

If `bbox` is missing on a site, that indication will not draw a box (pipeline should always emit `bbox`).

## UI notes

- Dark viewport; IBM Plex for UI type.  
- Legend at the top tracks the current clamp range.  
- Stats show excavation count, flat-face count, vertex count, mean Δ.  
- Narrow viewports stack the panel above the canvas (`App.css` breakpoint).

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Blank canvas / “Loading geometry…” stuck | Ensure `public/models/*` exist; run the pipeline |
| Console: deviation length ≠ vertex count | Regenerate with matching `modified.glb` and `deviation.bin` |
| Boxes float away from scoops | Center/rotation mismatch — boxes must stay inside `OverlayScene`’s offset group |
| Hover label missing | Confirm `bbox` on sites; check that **Indication boxes** is enabled |
| Typecheck fails | `cd viewer && npx tsc --noEmit` |
