/**
 * v1.1 feature flags. Default off so the M0 shell matches the v1.0 UI.
 * Later milestones may read overrides from settings; do not flip these here
 * until that milestone lands.
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
