/**
 * v1.1 feature flags. Defaults stay off so the shell matches the v1.0 UI.
 * The clarifier is implemented; set VITE_FEATURE_CLARIFY=true to mount it.
 */
export const features: {
  clarify: boolean
  jev: boolean
  verify: boolean
} = {
  clarify: false,
  jev: false,
  verify: false,
}

export function flagEnabled(name: keyof typeof features, override?: string | boolean): boolean {
  if (override === true || override === '1' || override === 'true') return true
  if (override === false || override === '0' || override === 'false') return false
  return features[name]
}
