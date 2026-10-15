/** Paths the Electron shell may forward to the local sidecar. */

export function allowedSidecarPath(method: string, requestPath: string): boolean {
  const pathOnly = (requestPath.split('?')[0] || '/').trim()
  if (method === 'DELETE') return /^\/library\/items\/\d+$/.test(pathOnly)
  if (method !== 'GET' && method !== 'POST') return false
  if (pathOnly === '/decision/logs') return method === 'GET'
  if (pathOnly === '/decision/shadow') return method === 'POST'
  if (pathOnly === '/library/items') return method === 'GET'
  if (pathOnly === '/library/import' || pathOnly === '/index/rebuild' || pathOnly === '/search') {
    return method === 'POST'
  }
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
