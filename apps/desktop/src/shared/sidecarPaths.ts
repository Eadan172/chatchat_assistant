/** Paths the Electron shell may forward to the local sidecar. */

export function allowedSidecarPath(method: string, requestPath: string): boolean {
  const pathOnly = (requestPath.split('?')[0] || '/').trim()

  // M7: conversation memory, folders, projects (GET/POST/PATCH/DELETE).
  if (/^\/(conversations|folders|projects)(\/.*)?$/.test(pathOnly)) {
    if (method === 'DELETE') {
      return /^\/(conversations|folders|projects)\/\d+$/.test(pathOnly)
    }
    return method === 'GET' || method === 'POST' || method === 'PATCH'
  }
  // M7: unified import channels.
  if (pathOnly === '/import/documents') return method === 'POST'
  if (/^\/import\/sources(\/[^/]+(\/(browse|import))?)?$/.test(pathOnly)) {
    return method === 'GET' || method === 'POST'
  }
  // M7: style catalog, preferences, deployment info.
  if (pathOnly === '/styles' || pathOnly === '/settings/app' || pathOnly === '/settings/ui') {
    return method === 'GET' || method === 'POST'
  }

  if (method === 'DELETE') {
    return /^\/library\/items\/\d+$/.test(pathOnly) || /^\/connectors\/\d+$/.test(pathOnly) || pathOnly === '/settings/ima'
  }
  if (method !== 'GET' && method !== 'POST') return false
  if (pathOnly === '/decision/logs') return method === 'GET'
  if (pathOnly === '/decision/shadow') return method === 'POST'
  if (pathOnly === '/library/items') return method === 'GET'
  if (
    pathOnly === '/library/import' ||
    pathOnly === '/index/rebuild' ||
    pathOnly === '/search' ||
    pathOnly === '/verify' ||
    pathOnly === '/ask'
  ) {
    return method === 'POST'
  }
  if (/^\/verify\/\d+$/.test(pathOnly)) return method === 'GET'
  if (pathOnly === '/connectors' || pathOnly === '/connectors/ima/bases' || pathOnly === '/connectors/ima/folders') {
    return method === 'GET'
  }
  if (pathOnly === '/connectors/ima/search' || pathOnly === '/connectors/ima/import') return method === 'POST'
  if (/^\/connectors\/\d+\/status$/.test(pathOnly)) return method === 'GET'
  if (/^\/connectors\/\d+\/sync$/.test(pathOnly)) return method === 'POST'
  if (pathOnly === '/settings/ima' || pathOnly === '/settings/ask') return method === 'GET' || method === 'POST'
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
