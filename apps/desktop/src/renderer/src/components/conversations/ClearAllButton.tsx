import { useState } from 'react'
import { useI18n } from '../../i18n'
import { errorMessage, sidecarRequest } from '../clarify/client'

interface ClearResult {
  deleted?: {
    conversations?: number
    messages?: number
    folders?: number
    projects?: number
    clarify_sessions?: number
    decision_logs?: number
  }
  backup_file?: string
  backup_path?: string
}

export function ClearAllButton({ onCleared }: { onCleared?: () => void | Promise<void> }) {
  const { t, tf } = useI18n()
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function clear() {
    setBusy(true)
    setError(null)
    setMessage(null)
    try {
      const result = await sidecarRequest('POST', '/conversations/clear', { confirm: 'DELETE_ALL' })
      if (!result.ok) throw new Error(errorMessage(result.data, t('清空失败')))
      const data = (result.data || {}) as ClearResult
      const deleted = data.deleted || {}
      const summary = tf('已清空 {n} 条对话、{m} 条消息', {
        n: deleted.conversations ?? 0,
        m: deleted.messages ?? 0,
      })
      setMessage(
        data.backup_file
          ? `${summary} · ${tf('备份文件：{name}', { name: data.backup_file })}`
          : summary,
      )
      setConfirming(false)
      await onCleared?.()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t('清空失败'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-4 rounded-xl bg-rose-50/70 px-3 py-2 text-xs ring-1 ring-rose-200" data-testid="clear-all">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium text-rose-700">{t('清空全部对话')}</span>
        {!confirming ? (
          <button
            type="button"
            data-testid="clear-all-start"
            onClick={() => {
              setConfirming(true)
              setMessage(null)
              setError(null)
            }}
            className="rounded-full px-3 py-1 text-rose-700 ring-1 ring-rose-300"
          >
            {t('清空全部对话')}
          </button>
        ) : null}
      </div>

      {confirming ? (
        <div className="mt-2 space-y-2">
          <p className="leading-relaxed text-rose-800">
            {t('将删除全部对话与消息，并清空文件夹、项目和澄清状态。删除前会自动备份。')}
          </p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              data-testid="clear-all-confirm"
              disabled={busy}
              onClick={() => void clear()}
              className="rounded-full bg-rose-600 px-3 py-1 text-white disabled:opacity-50"
            >
              {busy ? t('处理中…') : t('确认清空')}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => setConfirming(false)}
              className="rounded-full px-3 py-1 text-slate-600 ring-1 ring-slate-200"
            >
              {t('取消')}
            </button>
          </div>
        </div>
      ) : null}

      {message ? (
        <p className="mt-2 text-sage" data-testid="clear-all-result">
          {message}
        </p>
      ) : null}
      {error ? <p className="mt-2 text-rose-700">{error}</p> : null}
    </div>
  )
}
