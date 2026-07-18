import { C } from '@/lib/tokens'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { API_BASE } from '@/lib/config'

export const Route = createFileRoute('/login')({ component: LoginPage })

// Replaces the old Next.js `/api/login` route handler: authenticate against the backend
// and set the `ims_token` / `ims_user` cookies client-side (they are intentionally
// JS-readable so the Bearer token can be attached to API calls).
function setSessionCookies(token: string, user: unknown) {
  const maxAge = 60 * 60 * 24 * 30 // 30 days
  const secure = location.protocol === 'https:' ? '; secure' : ''
  document.cookie = `ims_token=${token}; path=/; max-age=${maxAge}; samesite=lax${secure}`
  document.cookie = `ims_user=${encodeURIComponent(JSON.stringify(user))}; path=/; max-age=${maxAge}; samesite=lax${secure}`
}

function LoginPage() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError]       = useState('')
  const [loading, setLoading]   = useState(false)
  const navigate = useNavigate()

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_BASE}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      if (res.ok) {
        const { token, user } = await res.json()
        setSessionCookies(token, user)
        navigate({ to: '/' })
      } else {
        setError('Invalid username or password')
        setLoading(false)
      }
    } catch {
      setError('Network error — is the server running?')
      setLoading(false)
    }
  }

  const ready = username.trim() && password && !loading

  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: C.ink }}>
      <div style={{ background: 'white', borderRadius: '12px', padding: '40px', width: '360px', boxShadow: '0 20px 60px rgba(0,0,0,0.4)' }}>
        {/* Logo */}
        <div style={{ marginBottom: '28px', textAlign: 'center' }}>
          <div style={{ fontSize: '22px', fontWeight: 700, color: C.ink, letterSpacing: '-0.5px' }}>
            ros<span style={{ color: C.indigo }}>etta</span>
            <span style={{ fontSize: '13px', fontWeight: 600, color: C.faint, marginLeft: '6px' }}>IMS</span>
          </div>
          <p style={{ fontSize: '13px', color: C.muted, marginTop: '6px' }}>Inventory Management System</p>
        </div>

        <form onSubmit={handleSubmit}>
          <div style={{ marginBottom: '12px' }}>
            <label style={{ display: 'block', fontSize: '12px', fontWeight: 600, color: '#374151', marginBottom: '4px' }}>
              Username
            </label>
            <input
              type="text"
              placeholder="e.g. seph"
              value={username}
              onChange={e => setUsername(e.target.value)}
              autoFocus
              autoComplete="username"
              style={{
                width: '100%', padding: '10px 14px', fontSize: '14px',
                border: '1px solid #E2E8F0', borderRadius: '8px', outline: 'none',
                boxSizing: 'border-box', color: C.ink,
              }}
            />
          </div>

          <div style={{ marginBottom: '16px' }}>
            <label style={{ display: 'block', fontSize: '12px', fontWeight: 600, color: '#374151', marginBottom: '4px' }}>
              Password
            </label>
            <input
              type="password"
              placeholder="Password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              autoComplete="current-password"
              style={{
                width: '100%', padding: '10px 14px', fontSize: '14px',
                border: '1px solid #E2E8F0', borderRadius: '8px', outline: 'none',
                boxSizing: 'border-box', color: C.ink,
              }}
            />
          </div>

          {error && (
            <p style={{ fontSize: '12px', color: '#EF4444', marginBottom: '12px', padding: '8px 10px', background: C.badBg, borderRadius: '6px' }}>
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={!ready}
            style={{
              width: '100%', padding: '11px', fontSize: '14px', fontWeight: 600,
              background: ready ? C.indigo : C.line,
              color: ready ? 'white' : C.faint,
              border: 'none', borderRadius: '8px', cursor: ready ? 'pointer' : 'default',
              transition: 'background 0.15s',
            }}
          >
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>

        <p style={{ fontSize: '11px', color: C.faint, textAlign: 'center', marginTop: '20px' }}>
          Algo Group · Ohana Animal Hospital
        </p>
      </div>
    </div>
  )
}
