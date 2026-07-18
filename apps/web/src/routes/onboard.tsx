import { C } from '@/lib/tokens'
import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { API_BASE } from '@/lib/config'

export const Route = createFileRoute('/onboard')({ component: OnboardPage })

interface Invite {
  valid: boolean
  expired: boolean
  email: string | null
  display_name: string
  role: string
  role_label: string
  invited_by: string | null
}

const card: React.CSSProperties = {
  background: 'white', borderRadius: '12px', padding: '36px', width: '400px',
  boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
}
const field: React.CSSProperties = {
  width: '100%', padding: '10px 14px', fontSize: '14px', border: '1px solid #E2E8F0',
  borderRadius: '8px', outline: 'none', boxSizing: 'border-box', color: C.ink,
}
const label: React.CSSProperties = { display: 'block', fontSize: '12px', fontWeight: 600, color: '#374151', marginBottom: '4px' }

function Logo() {
  return (
    <div style={{ marginBottom: '24px', textAlign: 'center' }}>
      <div style={{ fontSize: '22px', fontWeight: 700, color: C.ink, letterSpacing: '-0.5px' }}>
        ros<span style={{ color: C.indigo }}>etta</span>
        <span style={{ fontSize: '13px', fontWeight: 600, color: C.faint, marginLeft: '6px' }}>IMS</span>
      </div>
    </div>
  )
}

function OnboardPage() {
  const token = new URLSearchParams(window.location.search).get('token') ?? ''

  const [invite, setInvite] = useState<Invite | null>(null)
  const [loadErr, setLoadErr] = useState<string>('')
  const [loading, setLoading] = useState(true)

  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!token) { setLoadErr('Missing invite token.'); setLoading(false); return }
    fetch(`${API_BASE}/auth/invite/${encodeURIComponent(token)}`)
      .then(async r => {
        if (!r.ok) { setLoadErr((await r.json().catch(() => ({}))).detail ?? 'This invite link is invalid or has already been used.'); return }
        const d: Invite = await r.json()
        setInvite(d)
        setEmail(d.email ?? '')
        setDisplayName(d.display_name ?? '')
      })
      .catch(() => setLoadErr('Could not reach the server.'))
      .finally(() => setLoading(false))
  }, [token])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    if (password !== confirm) { setError('Passwords do not match'); return }
    if (password.length < 6) { setError('Password must be at least 6 characters'); return }
    setSubmitting(true)
    try {
      const r = await fetch(`${API_BASE}/auth/accept-invite`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, username, display_name: displayName, email, password }),
      })
      if (r.ok) {
        const { token: jwt, user } = await r.json()
        const maxAge = 60 * 60 * 24 * 30
        document.cookie = `ims_token=${encodeURIComponent(jwt)}; path=/; max-age=${maxAge}`
        document.cookie = `ims_user=${encodeURIComponent(JSON.stringify(user))}; path=/; max-age=${maxAge}`
        window.location.href = '/'
      } else {
        setError((await r.json().catch(() => ({}))).detail ?? 'Could not complete onboarding')
        setSubmitting(false)
      }
    } catch { setError('Network error'); setSubmitting(false) }
  }

  const wrap = (children: React.ReactNode) => (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: C.ink }}>
      <div style={card}><Logo />{children}</div>
    </div>
  )

  if (loading) return wrap(<p style={{ fontSize: '13px', color: C.muted, textAlign: 'center' }}>Checking your invite…</p>)

  if (loadErr || !invite) return wrap(
    <div style={{ textAlign: 'center' }}>
      <p style={{ fontSize: '14px', fontWeight: 600, color: C.redInk }}>Invite unavailable</p>
      <p style={{ fontSize: '13px', color: C.muted, marginTop: '8px' }}>{loadErr || 'This invite link is invalid or has already been used.'}</p>
      <a href="/login" style={{ fontSize: '13px', color: C.indigo, display: 'inline-block', marginTop: '16px' }}>Go to sign in</a>
    </div>
  )

  if (invite.expired) return wrap(
    <div style={{ textAlign: 'center' }}>
      <p style={{ fontSize: '14px', fontWeight: 600, color: C.amberInk }}>This invite has expired</p>
      <p style={{ fontSize: '13px', color: C.muted, marginTop: '8px' }}>Ask an admin to resend your invite, then use the new link.</p>
    </div>
  )

  const ready = username.trim() && email.trim() && password && confirm && !submitting

  return wrap(
    <>
      <p style={{ fontSize: '13px', color: C.muted, textAlign: 'center', marginBottom: '4px' }}>
        {invite.invited_by ? <>{invite.invited_by} invited you</> : 'You’ve been invited'} as a{' '}
        <span style={{ background: C.primaryBg, color: C.indigoInk, fontWeight: 600, padding: '1px 8px', borderRadius: '99px' }}>{invite.role_label}</span> user.
      </p>
      <p style={{ fontSize: '12px', color: C.faint, textAlign: 'center', marginBottom: '20px' }}>Set up your account to continue.</p>
      <form onSubmit={submit}>
        <div style={{ marginBottom: '11px' }}>
          <label style={label}>Username</label>
          <input style={field} value={username} onChange={e => setUsername(e.target.value)} placeholder="choose a username" autoFocus autoComplete="username" />
        </div>
        <div style={{ marginBottom: '11px' }}>
          <label style={label}>Full name</label>
          <input style={field} value={displayName} onChange={e => setDisplayName(e.target.value)} placeholder="Your name" autoComplete="name" />
        </div>
        <div style={{ marginBottom: '11px' }}>
          <label style={label}>Email</label>
          <input style={field} type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="you@example.com" autoComplete="email" />
        </div>
        <div style={{ marginBottom: '11px' }}>
          <label style={label}>Password</label>
          <input style={field} type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="min 6 characters" autoComplete="new-password" />
        </div>
        <div style={{ marginBottom: '16px' }}>
          <label style={label}>Confirm password</label>
          <input style={field} type="password" value={confirm} onChange={e => setConfirm(e.target.value)} placeholder="re-enter password" autoComplete="new-password" />
        </div>
        {error && <p style={{ fontSize: '12px', color: '#EF4444', marginBottom: '12px', padding: '8px 10px', background: C.badBg, borderRadius: '6px' }}>{error}</p>}
        <button type="submit" disabled={!ready}
          style={{ width: '100%', padding: '11px', fontSize: '14px', fontWeight: 600, background: ready ? C.indigo : C.line, color: ready ? 'white' : C.faint, border: 'none', borderRadius: '8px', cursor: ready ? 'pointer' : 'default' }}>
          {submitting ? 'Creating account…' : 'Create account & sign in'}
        </button>
      </form>
    </>
  )
}
