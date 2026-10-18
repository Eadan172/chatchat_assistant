import { useEffect, useState } from 'react'
import { flagEnabled } from '../../shared/features'
import { I18nProvider, useI18n, type Locale } from './i18n'
import { ChatPanel } from './components/chat/ChatPanel'
import { ClarifyPanel } from './components/clarify/ClarifyPanel'
import { ConversationsView } from './components/conversations/ConversationsView'
import { DecisionSettings } from './components/decision/DecisionSettings'
import { ImaPanel } from './components/connectors/ImaPanel'
import { LibraryPanel } from './components/library/LibraryPanel'
import { SettingsView } from './components/settings/SettingsView'

interface HealthState {
  ok?: boolean
  version?: string
  error?: string
}

const clarifyOn = flagEnabled('clarify', import.meta.env.VITE_FEATURE_CLARIFY)
const jevOn = flagEnabled('jev', import.meta.env.VITE_FEATURE_JEV)
const verifyOn = flagEnabled('verify', import.meta.env.VITE_FEATURE_VERIFY)
const imaOn = flagEnabled('ima', import.meta.env.VITE_FEATURE_IMA)
const askOn = flagEnabled('ask', import.meta.env.VITE_FEATURE_ASK)

type View = 'workspace' | 'conversations' | 'settings'

function Shell() {
  const { t, locale, setLocale } = useI18n()
  const [health, setHealth] = useState<HealthState | null>(null)
  const [view, setView] = useState<View>(() => {
    const params = new URLSearchParams(window.location.search)
    const requested = params.get('view') || window.location.hash.replace('#', '')
    return requested === 'conversations' || requested === 'settings' ? requested : 'workspace'
  })

  useEffect(() => {
    if (window.desktop) {
      void window.desktop.getHealth().then((payload) => setHealth(payload))
      return
    }
    const base = import.meta.env.VITE_SIDECAR_URL
    const token = import.meta.env.VITE_SIDECAR_TOKEN
    if (!base) return
    void fetch(`${base}/health`, { headers: token ? { 'x-sidecar-token': token } : {} })
      .then((response) => response.json())
      .then((payload) => setHealth(payload))
      .catch((error: unknown) => {
        setHealth({ ok: false, error: error instanceof Error ? error.message : t('sidecar 未连接') })
      })
  }, [t])

  const nav: { id: View; label: string }[] = [
    { id: 'workspace', label: t('知识库') },
    { id: 'conversations', label: t('对话') },
    { id: 'settings', label: t('设置') },
  ]

  return (
    <main
      className="min-h-screen bg-mist text-ink"
      data-clarify={String(clarifyOn)}
      data-jev={String(jevOn)}
      data-verify={String(verifyOn)}
      data-ima={String(imaOn)}
      data-ask={String(askOn)}
      data-locale={locale}
    >
      <div className="mx-auto max-w-6xl px-6 py-8">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-sm text-sage">{t('本地知识库')}</p>
            <h1 className="mt-1 text-3xl font-semibold">{t('知识引擎')}</h1>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <nav className="flex rounded-full bg-white/70 p-1 text-sm ring-1 ring-pool">
              {nav.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setView(item.id)}
                  className={`rounded-full px-3 py-1 transition ${view === item.id ? 'bg-sage text-white' : 'text-sage hover:bg-pool/50'}`}
                >
                  {item.label}
                </button>
              ))}
            </nav>
            <div className="flex rounded-full bg-white/70 p-1 text-xs ring-1 ring-pool" title={t('界面语言')}>
              {(['zh', 'en'] as Locale[]).map((code) => (
                <button
                  key={code}
                  type="button"
                  onClick={() => setLocale(code)}
                  className={`rounded-full px-3 py-1 ${locale === code ? 'bg-ink text-white' : 'text-slate-600'}`}
                >
                  {code === 'zh' ? '中文' : 'EN'}
                </button>
              ))}
            </div>
            <p className="text-sm text-slate-600">
              {health == null && t('等待 sidecar')}
              {health?.ok && `${t('sidecar 已连接')}${health.version ? ` · ${health.version}` : ''}`}
              {health && !health.ok && (health.error ?? t('sidecar 未连接'))}
            </p>
          </div>
        </header>

        <div className="mt-6">
          {view === 'workspace' ? (
            <>
              {imaOn ? <ImaPanel /> : null}
              <div className="grid items-start gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
                <LibraryPanel verifyOn={verifyOn} />
                <ChatPanel verifyOn={verifyOn} imaOn={imaOn} askOn={askOn} />
              </div>
              {clarifyOn ? <div className="mt-4"><ClarifyPanel /></div> : null}
              {jevOn ? <div className="mt-4"><DecisionSettings /></div> : null}
            </>
          ) : null}
          {view === 'conversations' ? <ConversationsView /> : null}
          {view === 'settings' ? <SettingsView /> : null}
        </div>
      </div>
    </main>
  )
}

export function App() {
  return (
    <I18nProvider>
      <Shell />
    </I18nProvider>
  )
}
