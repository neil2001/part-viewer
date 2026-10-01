export type SiteBBox = {
  center: [number, number, number]
  length_mm: number
  width_mm: number
  depth_mm: number
  /** Quaternion [x, y, z, w] for local frame (length, width, depth). */
  quaternion: [number, number, number, number]
}

export type Site = {
  kind: string
  band: string
  diameter_mm: number
  depth_mm: number
  point: [number, number, number]
  normal?: [number, number, number]
  bbox: SiteBBox
}

export type Meta = {
  vertex_count: number
  max_deviation_mm: number
  mean_deviation_mm: number
  excavation_count: number
  noise_sigma_mm: number
  units: string
  sites?: Site[]
}
