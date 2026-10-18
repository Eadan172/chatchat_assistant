/** Open an http(s) link from the renderer: Electron shell first, browser tab otherwise. */
export function openLink(url?: string | null): void {
  const target = (url || '').trim()
  if (!/^https?:\/\//i.test(target)) return
  if (window.desktop?.openExternal) {
    void window.desktop.openExternal(target)
    return
  }
  window.open(target, '_blank', 'noopener,noreferrer')
}

export function isHttpLink(url?: string | null): boolean {
  return /^https?:\/\//i.test((url || '').trim())
}
