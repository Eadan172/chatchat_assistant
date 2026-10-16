/**
 * v1.1 feature flags. Defaults stay off so the shell matches the v1.0 UI.
 * clarify: set VITE_FEATURE_CLARIFY=true to mount the clarifier.
 * jev: set VITE_FEATURE_JEV=true to mount decision-layer settings.
 * verify: set VITE_FEATURE_VERIFY=true to show 查证 and auto-verify.
 * ima: set VITE_FEATURE_IMA=true to mount the Tencent ima connector.
 * ask: set VITE_FEATURE_ASK=true to mount the end-to-end ask flow.
 * The values in this object stay false.
 */
export const features: {
  clarify: boolean
  jev: boolean
  verify: boolean
  ima: boolean
  ask: boolean
} = {
  clarify: false,
  jev: false,
  verify: false,
  ima: false,
  ask: false,
}

export function flagEnabled(name: keyof typeof features, override?: string | boolean): boolean {
  if (override === true || override === '1' || override === 'true') return true
  if (override === false || override === '0' || override === 'false') return false
  return features[name]
}
