import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { app, BrowserWindow, ipcMain, shell } from 'electron'
import log from 'electron-log/main'
import { sidecarBaseUrl, sidecarFetch, sidecarToken, startSidecar, stopSidecar, streamSidecar } from './sidecar'

// Single-machine deployment: keep the Chromium profile, logs and crash dumps
// inside the repository instead of %APPDATA% when CHATCHAT_DATA_DIR is set.
const localDataDir = process.env.CHATCHAT_DATA_DIR
if (localDataDir) {
  for (const dir of [localDataDir, path.join(localDataDir, 'logs'), path.join(localDataDir, 'crash')]) {
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true })
  }
  app.setPath('userData', localDataDir)
  app.setPath('sessionData', localDataDir)
  app.setPath('logs', path.join(localDataDir, 'logs'))
  app.setPath('crashDumps', path.join(localDataDir, 'crash'))
}

log.initialize()

// Remote desktop / VM sessions have no usable GPU; software rendering keeps the window alive.
if (process.env.CHATCHAT_DISABLE_GPU === '1') {
  app.disableHardwareAcceleration()
  app.commandLine.appendSwitch('disable-gpu')
  app.commandLine.appendSwitch('disable-gpu-compositing')
  app.commandLine.appendSwitch('in-process-gpu')
}

let sidecarError: string | null = null

function createWindow(): void {
  const win = new BrowserWindow({
    width: 1200,
    height: 820,
    show: false,
    backgroundColor: '#e7f2ec',
    title: '知识引擎',
    webPreferences: {
      preload: path.join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  })
  win.on('ready-to-show', () => win.show())
  // Verification hook: CHATCHAT_SHOT=<png path> captures the rendered window.
  if (process.env.CHATCHAT_SHOT) {
    win.webContents.once('did-finish-load', () => {
      setTimeout(() => {
        void win
          .capturePage()
          .then((image) => writeFileSync(process.env.CHATCHAT_SHOT as string, image.toPNG()))
          .catch((error) => log.error('[shot] capture failed', error))
      }, 8000)
    })
  }
  if (process.env.ELECTRON_RENDERER_URL) {
    void win.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    void win.loadFile(path.join(__dirname, '../renderer/index.html'))
  }
}

ipcMain.handle(
  'sidecar:request',
  async (_event, payload: { method?: string; path?: string; body?: unknown }) =>
    sidecarFetch(String(payload?.method || 'GET'), String(payload?.path || '/'), payload?.body),
)

ipcMain.handle(
  'sidecar:stream',
  async (event, payload: { path?: string; body?: unknown; channel?: string }) => {
    const channel = String(payload?.channel || '')
    if (!/^(chat|ask)-\d+-\d+$/.test(channel)) return { ok: false, status: 0, error: 'bad channel' }
    return streamSidecar(String(payload?.path || '/chat'), payload?.body, (item) => {
      if (!event.sender.isDestroyed()) event.sender.send(channel, item)
    })
  },
)

ipcMain.handle('shell:openExternal', async (_event, target: unknown) => {
  let parsed: URL
  try {
    parsed = new URL(String(target || ''))
  } catch {
    return { ok: false, error: 'url not allowed' }
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    return { ok: false, error: 'url not allowed' }
  }
  await shell.openExternal(parsed.toString())
  return { ok: true }
})

ipcMain.handle('shell:openPath', async (_event, target: unknown) => {
  const root = path.resolve(app.getPath('userData'), 'library')
  const resolved = path.resolve(String(target || ''))
  if (resolved !== root && !resolved.startsWith(root + path.sep)) {
    return { ok: false, error: 'path not allowed' }
  }
  const error = await shell.openPath(resolved)
  return { ok: error === '', error: error || undefined }
})

ipcMain.handle('sidecar:health', async () => {
  if (!sidecarBaseUrl) {
    return { ok: false, error: sidecarError ?? 'sidecar not started' }
  }
  try {
    const response = await fetch(`${sidecarBaseUrl}/health`, {
      headers: { 'x-sidecar-token': sidecarToken },
    })
    return await response.json()
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : 'health check failed' }
  }
})

app.whenReady().then(async () => {
  try {
    const baseUrl = await startSidecar()
    log.info('[sidecar] ready', baseUrl)
  } catch (error) {
    sidecarError = error instanceof Error ? error.message : 'sidecar failed'
    log.error(sidecarError)
  }
  createWindow()
})

app.on('before-quit', () => stopSidecar())
app.on('window-all-closed', () => {
  stopSidecar()
  if (process.platform !== 'darwin') app.quit()
})
