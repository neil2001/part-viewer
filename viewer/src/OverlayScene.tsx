import { useEffect, useMemo, useState } from 'react'
import { useGLTF } from '@react-three/drei'
import * as THREE from 'three'
import { IndicationBoxes } from './IndicationBoxes'
import type { Site } from './types'

type Props = {
  showOriginal: boolean
  showModified: boolean
  showBoxes: boolean
  clampMm: number
  sites: Site[]
  onMaxDeviation: (v: number) => void
}


/** Blue → yellow → red ramp for deviation intensity. */
function heatColor(t: number, target: THREE.Color) {
  const x = Math.min(Math.max(t, 0), 1)
  if (x < 0.5) {
    const u = x / 0.5
    target.setRGB(0.08 + 0.82 * u, 0.28 + 0.55 * u, 0.85 - 0.55 * u)
  } else {
    const u = (x - 0.5) / 0.5
    target.setRGB(0.9 + 0.1 * u, 0.83 - 0.7 * u, 0.3 - 0.25 * u)
  }
}

function useDeviation(url: string, expectedCount: number | null) {
  const [data, setData] = useState<Float32Array | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetch(url)
      .then((r) => {
        if (!r.ok) throw new Error(`deviation.bin ${r.status}`)
        return r.arrayBuffer()
      })
      .then((buf) => {
        if (cancelled) return
        const arr = new Float32Array(buf)
        if (expectedCount != null && arr.length !== expectedCount) {
          console.warn(
            `deviation length ${arr.length} != vertex count ${expectedCount}`,
          )
        }
        setData(arr)
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message)
      })
    return () => {
      cancelled = true
    }
  }, [url, expectedCount])

  return { data, error }
}

function centerOffset(objects: THREE.Object3D[]): THREE.Vector3 {
  const box = new THREE.Box3()
  for (const obj of objects) box.expandByObject(obj)
  const center = new THREE.Vector3()
  box.getCenter(center)
  return center.multiplyScalar(-1)
}

export function OverlayScene({
  showOriginal,
  showModified,
  showBoxes,
  clampMm,
  sites,
  onMaxDeviation,
}: Props) {
  const original = useGLTF('/models/original.glb')
  const modified = useGLTF('/models/modified.glb')

  const modMesh = useMemo(() => {
    let found: THREE.Mesh | undefined
    modified.scene.traverse((obj) => {
      if (!found && (obj as THREE.Mesh).isMesh) {
        found = obj as THREE.Mesh
      }
    })
    return found
  }, [modified])

  const origMesh = useMemo(() => {
    let found: THREE.Mesh | undefined
    original.scene.traverse((obj) => {
      if (!found && (obj as THREE.Mesh).isMesh) {
        found = obj as THREE.Mesh
      }
    })
    return found
  }, [original])

  const expectedCount = modMesh
    ? modMesh.geometry.attributes.position!.count
    : null
  const { data: deviation } = useDeviation('/models/deviation.bin', expectedCount)

  const offset = useMemo(() => {
    const objs: THREE.Object3D[] = []
    if (origMesh) objs.push(origMesh)
    if (modMesh) objs.push(modMesh)
    return objs.length ? centerOffset(objs) : new THREE.Vector3()
  }, [origMesh, modMesh])

  const heatGeom = useMemo(() => {
    if (!modMesh || !deviation) return null
    const geom = modMesh.geometry.clone()
    const pos = geom.getAttribute('position')
    const n = pos.count
    const colors = new Float32Array(n * 3)
    const c = new THREE.Color()
    for (let i = 0; i < n; i++) {
      const d = i < deviation.length ? deviation[i]! : 0
      heatColor(clampMm > 1e-6 ? d / clampMm : 0, c)
      colors[i * 3] = c.r
      colors[i * 3 + 1] = c.g
      colors[i * 3 + 2] = c.b
    }
    geom.setAttribute('color', new THREE.BufferAttribute(colors, 3))
    return geom
  }, [modMesh, deviation, clampMm])

  useEffect(() => {
    if (!deviation) return
    let max = 0
    for (let i = 0; i < deviation.length; i++) {
      const v = deviation[i]!
      if (v > max) max = v
    }
    onMaxDeviation(max)
  }, [deviation, onMaxDeviation])

  const ghostMat = useMemo(
    () =>
      new THREE.MeshStandardMaterial({
        color: '#9aabbf',
        metalness: 0.55,
        roughness: 0.45,
        transparent: true,
        opacity: 0.22,
        depthWrite: false,
        side: THREE.DoubleSide,
        polygonOffset: true,
        polygonOffsetFactor: 1,
        polygonOffsetUnits: 1,
      }),
    [],
  )

  const heatMat = useMemo(
    () =>
      new THREE.MeshStandardMaterial({
        vertexColors: true,
        metalness: 0.25,
        roughness: 0.55,
        side: THREE.DoubleSide,
      }),
    [],
  )

  return (
    <group position={offset.toArray() as [number, number, number]}>
      {showOriginal && origMesh ? (
        <mesh
          geometry={origMesh.geometry}
          material={ghostMat}
          castShadow
          receiveShadow
        />
      ) : null}
      {showModified && heatGeom ? (
        <mesh geometry={heatGeom} material={heatMat} castShadow receiveShadow />
      ) : null}
      <IndicationBoxes sites={sites} visible={showBoxes} />
    </group>
  )
}

useGLTF.preload('/models/original.glb')
useGLTF.preload('/models/modified.glb')
