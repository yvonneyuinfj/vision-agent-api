import { create } from 'zustand'
import { fetchMe, logout, type AuthMe, type LoginResult } from '../api/auth'

interface AuthState {
  /** 登录态：null = 尚未探测 */
  loggedIn: boolean
  username: string
  /** 后端启动探测当前会话 */
  init: () => Promise<void>
  /** 退出登录 */
  signOut: () => Promise<void>
}

const EMPTY = { logged_in: false, username: '' } as AuthMe

function readMe(m?: AuthMe): { loggedIn: boolean; username: string } {
  const me = (m && typeof m.logged_in === 'boolean' ? m : EMPTY) as AuthMe
  return { loggedIn: me.logged_in, username: me.username || '' }
}

export const useAuthStore = create<AuthState>((set) => ({
  loggedIn: false,
  username: '',
  init: async () => {
    try {
      const me = await fetchMe()
      set(readMe(me))
    } catch {
      // 网络异常时保守视为未登录
      set({ loggedIn: false, username: '' })
    }
  },
  signOut: async () => {
    try {
      await logout()
    } finally {
      set({ loggedIn: false, username: '' })
    }
  },
}))

/** 供组件使用：登录成功调用，更新本地状态 */
export function applyLoggedIn(r: LoginResult): void {
  useAuthStore.setState({ loggedIn: !!r.logged_in, username: r.username || '' })
}