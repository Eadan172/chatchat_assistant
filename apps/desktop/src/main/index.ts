import path from 'node:path'
import { app, BrowserWindow, ipcMain } from 'electron'
import log from 'electron-log/main'
import { sidecarBaseUrl, sidecarToken, startSidecar, stopSidecar } from './sidecar'

log.initialize()

let sidecarError: string | null = null

function createWindow(): void {
  const win = new BrowserWindow({
    width: 1100,
    height: 760,
    show: false,
    title: '知识引擎',
    webPreferences: {
      preload: path.join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  })
  win.on('ready-to-show', () => win.show())
  if (process.env.ELECTRON_RENDERER_URL) {
    void win.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    void win.loadFile(path.join(__dirname, '../renderer/index.html'))
  }
}

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
