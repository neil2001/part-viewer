import { useMemo, useState } from 'react'
import { Html } from '@react-three/drei'
import * as THREE from 'three'
import type { Site } from './types'

type Props = {
  sites: Site[]
  visible: boolean
}

function IndicationBox({ site }: { site: Site }) {
  const [hovered, setHovered] = useState(false)
  const { center, length_mm, width_mm, depth_mm, quaternion } = site.bbox

  const { edges, hitGeom, quat } = useMemo(() => {
    const box = new THREE.BoxGeometry(length_mm, width_mm, depth_mm)
    const edgesGeom = new THREE.EdgesGeometry(box)
    // Thicker hit volume along depth so face-on views are easier to hover
    const hit = new THREE.BoxGeometry(
      length_mm * 1.05,
      width_mm * 1.05,
      Math.max(depth_mm * 4, 6),
    )
    return {
      edges: edgesGeom,
      hitGeom: hit,
      quat: new THREE.Quaternion(
        quaternion[0],
        quaternion[1],
        quaternion[2],
        quaternion[3],
      ),
    }
  }, [length_mm, width_mm, depth_mm, quaternion])

  const lineColor = hovered ? '#ff8a5c' : '#ff5a36'
  const lineWidth = hovered ? 2 : 1

  return (
    <group position={center} quaternion={quat}>
      <lineSegments geometry={edges} renderOrder={2}>
        <lineBasicMaterial
          color={lineColor}
          linewidth={lineWidth}
          depthTest
          transparent
          opacity={hovered ? 1 : 0.9}
        />
      </lineSegments>
      <mesh
        geometry={hitGeom}
        onPointerOver={(e) => {
          e.stopPropagation()
          setHovered(true)
          document.body.style.cursor = 'pointer'
        }}
        onPointerOut={() => {
          setHovered(false)
          document.body.style.cursor = 'auto'
        }}
      >
        <meshBasicMaterial
          transparent
          opacity={0}
          depthWrite={false}
          side={THREE.DoubleSide}
        />
      </mesh>
      {hovered ? (
        <Html
          center
          distanceFactor={280}
          style={{ pointerEvents: 'none', userSelect: 'none' }}
        >
          <div
            className="mono"
            style={{
              background: 'rgba(12, 15, 20, 0.92)',
              border: '1px solid #ff5a36',
              color: '#e8edf5',
              padding: '6px 10px',
              fontSize: 12,
              whiteSpace: 'nowrap',
              borderRadius: 2,
              boxShadow: '0 4px 18px rgba(0,0,0,0.45)',
            }}
          >
            <div style={{ color: '#ff8a5c', fontSize: 10, marginBottom: 2 }}>
              {site.kind.replace('_', ' ')} · {site.band}
            </div>
            L {length_mm.toFixed(1)} · W {width_mm.toFixed(1)} · D{' '}
            {depth_mm.toFixed(2)} mm
          </div>
        </Html>
      ) : null}
    </group>
  )
}

export function IndicationBoxes({ sites, visible }: Props) {
  if (!visible || sites.length === 0) return null
  return (
    <group>
      {sites.map((site, i) => (
        <IndicationBox key={`${site.kind}-${i}`} site={site} />
      ))}
    </group>
  )
}
