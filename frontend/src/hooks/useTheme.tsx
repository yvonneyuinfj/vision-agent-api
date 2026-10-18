import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { ANTD_THEME, type Theme } from '../constants/theme'

export { type Theme } from '../constants/theme'

export const THEME_KEY = 'aeroqc-theme'

interface ThemeContextValue {
  theme: Theme
  toggleTheme: () => void
}

const ThemeContext = createContext<ThemeContextValue>({
  theme: 'light',
  toggleTheme: () => {},
})

let themeTimer: number | undefined

function readSavedTheme(): Theme {
  try {
    // 浅色为默认主题（新ui.html）；仅显式保存过 dark 才走深色
    return localStorage.getItem(THEME_KEY) === 'dark' ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

/* 同步 <html data-theme> 并触发 480ms theme-anim 渐变；
   明暗图标由各调用点按 theme 条件渲染，不在此操作 DOM。 */
export function applyTheme(theme: Theme): void {
  const root = document.documentElement
  root.setAttribute('data-theme', theme)
  root.classList.add('theme-anim')
  window.clearTimeout(themeTimer)
  themeTimer = window.setTimeout(() => root.classList.remove('theme-anim'), 480)
}

/* render 前调用：首帧就使用已保存主题，避免 bundle 加载期暗色闪白 */
export function initTheme(): Theme {
  const theme = readSavedTheme()
  applyTheme(theme)
  return theme
}

export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext)
}

/* 主题根：持有唯一主题状态，同时把 antd 主题 token 随明暗切换联动 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(readSavedTheme)

  useEffect(() => {
    applyTheme(theme)
  }, [theme])

  const toggleTheme = useCallback(() => {
    setTheme((prev) => {
      const next: Theme = prev === 'dark' ? 'light' : 'dark'
      try {
        localStorage.setItem(THEME_KEY, next)
      } catch {
        /* private mode */
      }
      return next
    })
  }, [])

  const value = useMemo<ThemeContextValue>(() => ({ theme, toggleTheme }), [theme, toggleTheme])

  return (
    <ThemeContext.Provider value={value}>
      <ConfigProvider locale={zhCN} theme={ANTD_THEME[theme]}>
        {children}
      </ConfigProvider>
    </ThemeContext.Provider>
  )
}