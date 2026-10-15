/** Paths the Electron shell may forward to the local sidecar. */

export function allowedSidecarPath(method: string, requestPath: string): boolean {
  if (method !== 'GET' && method !== 'POST') return false
  const pathOnly = (requestPath.split('?')[0] || '/').trim()
  if (pathOnly === '/decision/logs') return method === 'GET'
  if (pathOnly === '/decision/shadow') return method === 'POST'
  if (
    pathOnly === '/chat' ||
    pathOnly === '/clarify/start' ||
    pathOnly === '/clarify/active' ||
    pathOnly === '/settings/decision'
  ) {
    return true
  }
  return /^\/clarify\/\d+(\/(answer|confirm))?$/.test(pathOnly)
}
