import { API_BASE } from './config'

export type Role = 'admin' | 'bizops' | 'data_entry'

export interface IMSUser {
  id: number
  username: string
  display_name: string
  role: Role
  role_label?: string
}

export const ROLE_LABELS: Record<Role, string> = {
  admin: 'Admin', bizops: 'BizOps', data_entry: 'Data Entry',
}

// Capability → roles that hold it. MUST mirror backend permissions.py — the backend is the
// real gate; this only drives what the UI shows/enables.
export const CAPABILITIES: Record<string, Role[]> = {
  catalogue_onboard: ['admin', 'bizops', 'data_entry'],
  product_edit:      ['admin', 'bizops', 'data_entry'],
  product_sensitive: ['admin', 'bizops'],
  catalogue_admin:   ['admin'],
  reference_admin:   ['admin'],
  sheet:             ['admin'],
  stock_import:      ['admin'],
  user_admin:        ['admin'],
  audit_view:        ['admin'],
  config_admin:      ['admin'],
}

/** Does the current user hold `capability`? */
export function can(capability: string): boolean {
  const u = getUser()
  return !!u && (CAPABILITIES[capability]?.includes(u.role) ?? false)
}

function parseCookie(name: string): string | null {
  if (typeof document === 'undefined') return null
  const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'))
  return match ? decodeURIComponent(match[1]) : null
}

export function getToken(): string | null {
  return parseCookie('ims_token')
}

export function getUser(): IMSUser | null {
  const raw = parseCookie('ims_user')
  if (!raw) return null
  try { return JSON.parse(raw) } catch { return null }
}

export function isAdmin(): boolean {
  return getUser()?.role === 'admin'
}

export function logout() {
  // Best-effort logout audit on the backend, then clear the session and bounce to login.
  const token = getToken()
  if (token) {
    try {
      fetch(`${API_BASE}/auth/logout`, { method: 'POST', headers: { Authorization: `Bearer ${token}` }, keepalive: true })
        .catch(() => {})
    } catch { /* ignore */ }
  }
  document.cookie = 'ims_token=; path=/; max-age=0'
  document.cookie = 'ims_user=; path=/; max-age=0'
  window.location.href = '/login'
}

/** Returns headers with Bearer token for fetch calls to the backend. */
export function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const token = getToken()
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...extra,
  }
}
