import { useEffect, useMemo, useState } from 'react'
import { Canvas } from '@react-three/fiber'
import { Bounds, Environment, OrbitControls } from '@react-three/drei'
import { OverlayScene } from './OverlayScene'
import type { Meta } from './types'
import './App.css'

const DEFAULT_CLAMP = 3.5

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [showOriginal, setShowOriginal] = useState(true)
  const [showModified, setShowModified] = useState(true)
  const [showBoxes, setShowBoxes] = useState(true)
  const [clampMm, setClampMm] = useState(DEFAULT_CLAMP)
  const [maxDev, setMaxDev] = useState(0)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/models/meta.json')
      .then((r) => {
        if (!r.ok) throw new Error(`meta.json ${r.status}`)
        return r.json()
      })
      .then((m: Meta) => {
        setMeta(m)
        setClampMm(Math.min(DEFAULT_CLAMP, Math.max(0.2, m.max_deviation_mm)))
      })
      .catch((e: Error) => setError(e.message))
  }, [])

  const subtitle = useMemo(() => {
    if (!meta) return 'Loading geometry…'
    const siteList = meta.sites ?? []
    const nFlat = siteList.filter((s) => s.kind.startsWith('flat_')).length
    return `${meta.excavation_count} excavations (${nFlat} flat) · noise σ ${meta.noise_sigma_mm} mm · max Δ ${meta.max_deviation_mm.toFixed(2)} mm`
  }, [meta])

  const sites = meta?.sites ?? []

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <div className="eyebrow mono">CASTING REPAIR INSPECTION</div>
          <h1>Impeller weldmap</h1>
          <p className="subtitle mono">{subtitle}</p>
        </div>
        <div className="legend">
          <div className="ramp" aria-hidden />
          <div className="legend-labels mono">
            <span>0</span>
            <span>{clampMm.toFixed(2)} mm</span>
          </div>
          <div className="legend-note mono">
            clamp {clampMm.toFixed(2)} mm
            {maxDev > 0 ? ` · mesh max ${maxDev.toFixed(2)} mm` : ''}
          </div>
        </div>
      </header>

      <aside className="panel">
        <div className="panel-title mono">Layers</div>
        <label className="row">
          <input
            type="checkbox"
            checked={showOriginal}
            onChange={(e) => setShowOriginal(e.target.checked)}
          />
          <span>Original (ghost)</span>
        </label>
        <label className="row">
          <input
            type="checkbox"
            checked={showModified}
            onChange={(e) => setShowModified(e.target.checked)}
          />
          <span>Modified heatmap</span>
        </label>
        <label className="row">
          <input
            type="checkbox"
            checked={showBoxes}
            onChange={(e) => setShowBoxes(e.target.checked)}
          />
          <span>Indication boxes</span>
        </label>

        <div className="panel-title mono" style={{ marginTop: 18 }}>
          Heatmap clamp
        </div>
        <input
          className="slider"
          type="range"
          min={0.05}
          max={Math.max(meta?.max_deviation_mm ?? 8, 1)}
          step={0.05}
          value={clampMm}
          onChange={(e) => setClampMm(Number(e.target.value))}
        />
        <div className="hint mono">
          Drag low (~0.15) to inspect noise. Keep near 3–4 mm for scoops.
          Hover a box for L × W × D.
        </div>

        {meta && (
          <div className="stats mono">
            <div>
              <span>excavations</span>
              <strong>{meta.excavation_count}</strong>
            </div>
            <div>
              <span>flat faces</span>
              <strong>
                {sites.filter((s) => s.kind.startsWith('flat_')).length}
              </strong>
            </div>
            <div>
              <span>vertices</span>
              <strong>{meta.vertex_count.toLocaleString()}</strong>
            </div>
            <div>
              <span>mean Δ</span>
              <strong>{meta.mean_deviation_mm.toFixed(3)} mm</strong>
            </div>
          </div>
        )}

        {error && <div className="error">{error}</div>}
      </aside>

      <div className="viewport">
        <Canvas
          camera={{ position: [0, 0, 650], fov: 40, near: 0.1, far: 5000 }}
          gl={{ antialias: true, toneMappingExposure: 1.05 }}
          dpr={[1, 1.75]}
        >
          <color attach="background" args={['#0b0d10']} />
          <ambientLight intensity={0.35} />
          <directionalLight position={[220, 320, 180]} intensity={1.15} />
          <directionalLight position={[-180, 80, -220]} intensity={0.35} />
          <Environment preset="warehouse" environmentIntensity={0.35} />
          <Bounds fit clip observe margin={1.2}>
            {/* Impeller axis is X in the STEP; tip it so the eye looks into the bore. */}
            <group rotation={[0, Math.PI / 2, 0]}>
              <OverlayScene
                showOriginal={showOriginal}
                showModified={showModified}
                showBoxes={showBoxes}
                clampMm={clampMm}
                sites={sites}
                onMaxDeviation={setMaxDev}
              />
            </group>
          </Bounds>
          <OrbitControls makeDefault enableDamping dampingFactor={0.08} />
        </Canvas>
      </div>
    </div>
  )
}
