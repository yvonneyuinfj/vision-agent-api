import http, { unwrapResponse } from './http'

export interface AuthMe {
  logged_in: boolean
  username: string
}

export interface LoginResult {
  logged_in: boolean
  username: string
  expires_hours?: number
}

/** 当前登录态（供路由守卫初始化） */
export function fetchMe(): Promise<AuthMe> {
  return http.get<{ code: number; message: string; result: AuthMe }>('/auth/me').then(unwrapResponse)
}

/** 登录：成功即由后端下发 httpOnly 会话 Cookie */
export function login(username: string, password: string): Promise<LoginResult> {
  return http
    .post<{ code: number; message: string; result: LoginResult }>('/auth/login', { username, password })
    .then(unwrapResponse)
}

/** 登出：清除会话 Cookie */
export function logout(): Promise<{ logged_in: boolean }> {
  return http
    .post<{ code: number; message: string; result: { logged_in: boolean } }>('/auth/logout')
    .then(unwrapResponse)
}