import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { EN } from './dictionary'
import { sidecarRequest } from '../components/clarify/client'

export type Locale = 'zh' | 'en'

const STORAGE_KEY = 'chatchat.locale'

interface I18nValue {
  locale: Locale
  setLocale: (next: Locale) => void
  t: (zh: string) => string
  tf: (zh: string, vars: Record<string, string | number>) => string
}

const fallback: I18nValue = {
  locale: 'zh',
  setLocale: () => undefined,
  t: (zh) => zh,
  tf: (zh, vars) => interpolate(zh, vars),
}

const I18nContext = createContext<I18nValue>(fallback)

function interpolate(text: string, vars: Record<string, string | number>): string {
  return text.replace(/\{(\w+)\}/g, (match, key: string) =>
    key in vars ? String(vars[key]) : match,
  )
}

function readStored(): Locale | null {
  try {
    const fromUrl = new URLSearchParams(window.location.search).get('lang')
    if (fromUrl === 'en' || fromUrl === 'zh') {
      window.localStorage.setItem(STORAGE_KEY, fromUrl)
      return fromUrl
    }
    const raw = window.localStorage.getItem(STORAGE_KEY)
    return raw === 'en' || raw === 'zh' ? raw : null
  } catch {
    return null
  }
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(() => readStored() ?? 'zh')

  useEffect(() => {
    document.documentElement.lang = locale === 'zh' ? 'zh-CN' : 'en'
  }, [locale])

  // First run without a local choice: take the preference saved in DATA_DIR/config/ui.json.
  useEffect(() => {
    if (readStored()) return
    void sidecarRequest('GET', '/settings/ui').then((result) => {
      const saved = result.ok && result.data && typeof result.data === 'object'
        ? (result.data as { locale?: string }).locale
        : undefined
      if (saved === 'en' || saved === 'zh') setLocaleState(saved)
    })
  }, [])

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next)
    try {
      window.localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // Storage may be unavailable; the in-memory switch still applies.
    }
    void sidecarRequest('POST', '/settings/ui', { locale: next })
  }, [])

  const t = useCallback((zh: string) => (locale === 'zh' ? zh : EN[zh] ?? zh), [locale])
  const tf = useCallback(
    (zh: string, vars: Record<string, string | number>) =>
      interpolate(locale === 'zh' ? zh : EN[zh] ?? zh, vars),
    [locale],
  )

  const value = useMemo<I18nValue>(() => ({ locale, setLocale, t, tf }), [locale, setLocale, t, tf])
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

export function useI18n(): I18nValue {
  return useContext(I18nContext)
}
