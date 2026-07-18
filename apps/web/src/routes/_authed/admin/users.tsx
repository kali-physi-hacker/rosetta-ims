import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { authHeaders, getUser, can, ROLE_LABELS, type Role } from '@/lib/auth'
import { toast } from '@/lib/toast'
import { confirmDialog } from '@/lib/confirm'
import { API_BASE } from '@/lib/config'

const API = API_BASE

export const Route = createFileRoute('/_authed/admin/users')({ component: UsersAdminPage })

interface ManagedUser {
  id: number
  username: string
  display_name: string
  email: string | null
  role: Role
  role_label: string
  is_active: boolean
  invite_status: 'active' | 'invited' | 'inactive'
  invited_by: string | null
  created_at: string | null
  updated_at: string | null
  last_login_at: string | null
}

const ROLES: Role[] = ['admin', 'bizops', 'data_entry']
const ROLE_HINT: Record<Role, string> = {
  admin: 'Full access + user management',
  bizops: 'View + catalogue onboarding + all SKU edits',
  data_entry: 'Like BizOps, but cannot edit name / category / status / hero-SKU',
}

function fmt(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—'
    : d.toLocaleString('en-GB', { day: '2-digit', month: 'short', year: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function UsersAdminPage() {
  const [me, setMe] = useState(getUser())
  const [users, setUsers] = useState<ManagedUser[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<number | 'new' | 'invite' | null>(null)
  // create form
  const [nu, setNu] = useState({ username: '', display_name: '', password: '', role: 'bizops' as Role })
  // invite form
  const [inv, setInv] = useState({ email: '', display_name: '', role: 'bizops' as Role })
  const [lastInvite, setLastInvite] = useState<{ email: string; url: string } | null>(null)

  useEffect(() => { setMe(getUser()); load() }, [])

  async function inviteUser() {
    if (!inv.email.trim() || !inv.email.includes('@')) { toast.error('A valid email is required'); return }
    setBusy('invite')
    try {
      const r = await fetch(`${API}/users/invite`, {
        method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(inv),
      })
      const d = await r.json().catch(() => ({}))
      if (r.ok) {
        setLastInvite({ email: inv.email.trim(), url: d.invite_url })
        toast.success(`Invite created for ${inv.email.trim()} — email sending`)
        setInv({ email: '', display_name: '', role: inv.role })
        load()
      } else toast.error(d.detail ?? 'Invite failed')
    } finally { setBusy(null) }
  }

  async function resendInvite(u: ManagedUser) {
    setBusy(u.id)
    try {
      const r = await fetch(`${API}/users/${u.id}/resend-invite`, { method: 'POST', headers: authHeaders() })
      const d = await r.json().catch(() => ({}))
      if (r.ok) {
        setLastInvite({ email: u.email ?? '', url: d.invite_url })
        toast.success(`Invite re-sent to ${u.email}`)
        load()
      } else toast.error(d.detail ?? 'Resend failed')
    } finally { setBusy(null) }
  }

  async function load() {
    setLoading(true)
    try {
      const r = await fetch(`${API}/users`, { headers: authHeaders() })
      if (r.ok) setUsers((await r.json()).users ?? [])
      else if (r.status === 403) toast.error('Admin access required')
    } catch { toast.error('Could not load users') }
    finally { setLoading(false) }
  }

  async function createUser() {
    if (!nu.username.trim() || !nu.password) { toast.error('Username and password are required'); return }
    setBusy('new')
    try {
      const r = await fetch(`${API}/users`, {
        method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(nu),
      })
      if (r.ok) { toast.success(`Created ${nu.username}`); setNu({ username: '', display_name: '', password: '', role: 'bizops' }); load() }
      else { const e = await r.json().catch(() => ({})); toast.error(e.detail ?? 'Create failed') }
    } finally { setBusy(null) }
  }

  async function patchUser(u: ManagedUser, patch: Record<string, unknown>, label: string) {
    setBusy(u.id)
    try {
      const r = await fetch(`${API}/users/${u.id}`, {
        method: 'PATCH', headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(patch),
      })
      if (r.ok) { toast.success(label); load() }
      else { const e = await r.json().catch(() => ({})); toast.error(e.detail ?? 'Update failed') }
    } finally { setBusy(null) }
  }

  async function changeRole(u: ManagedUser, role: Role) {
    if (role === u.role) return
    const ok = await confirmDialog({
      title: 'Change role',
      message: `Change ${u.username} from ${ROLE_LABELS[u.role]} to ${ROLE_LABELS[role]}?`,
      confirmLabel: 'Change role',
    })
    if (!ok) return
    patchUser(u, { role }, `${u.username} → ${ROLE_LABELS[role]}`)
  }

  async function toggleActive(u: ManagedUser) {
    const ok = await confirmDialog({
      title: u.is_active ? 'Deactivate user' : 'Reactivate user',
      message: u.is_active
        ? `Deactivate ${u.username}? They will no longer be able to sign in.`
        : `Reactivate ${u.username}?`,
      confirmLabel: u.is_active ? 'Deactivate' : 'Reactivate',
      danger: u.is_active,
    })
    if (!ok) return
    patchUser(u, { is_active: !u.is_active }, u.is_active ? `Deactivated ${u.username}` : `Reactivated ${u.username}`)
  }

  async function resetPassword(u: ManagedUser) {
    const pw = window.prompt(`New password for ${u.username} (min 6 chars):`)
    if (!pw) return
    if (pw.length < 6) { toast.error('Password must be at least 6 characters'); return }
    patchUser(u, { password: pw }, `Password reset for ${u.username}`)
  }

  if (!can('user_admin')) {
    return (
      <div style={{ padding: '40px', maxWidth: '560px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 700, color: '#0F172A' }}>Users</h1>
        <div style={{ marginTop: '12px', padding: '14px 16px', background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: '8px', color: '#991B1B', fontSize: '13px' }}>
          <strong>Admin access required.</strong> Your role ({ROLE_LABELS[me?.role ?? 'bizops']}) cannot manage user accounts.
        </div>
      </div>
    )
  }

  const cell = { padding: '8px 12px', fontSize: '12px', color: '#334155', borderBottom: '1px solid #F1F5F9' } as const
  const th = { padding: '8px 12px', fontSize: '10px', fontWeight: 700, color: '#64748B', textTransform: 'uppercase' as const, letterSpacing: '0.05em', textAlign: 'left' as const, borderBottom: '1px solid #E2E8F0' }
  const input = { border: '1px solid #E2E8F0', borderRadius: '6px', padding: '6px 10px', fontSize: '12px' }

  return (
    <div style={{ padding: '28px 32px', maxWidth: '1080px' }}>
      <h1 style={{ fontSize: '20px', fontWeight: 700, color: '#0F172A', margin: 0 }}>User accounts</h1>
      <p style={{ fontSize: '12.5px', color: '#64748B', marginTop: '4px' }}>
        Create accounts and assign roles. Every change is recorded in the <a href="/admin/audit" style={{ color: '#6366F1' }}>Audit Log</a>.
      </p>

      {/* Invite by email */}
      <div style={{ marginTop: '18px', background: 'white', border: '1px solid #E2E8F0', borderRadius: '10px', padding: '14px 16px' }}>
        <div style={{ fontSize: '11px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '10px' }}>Invite by email</div>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}><span style={{ fontSize: '10px', color: '#94A3B8', fontWeight: 600 }}>Email</span>
            <input style={{ ...input, width: '230px' }} type="email" value={inv.email} onChange={e => setInv({ ...inv, email: e.target.value })} placeholder="jane@example.com" /></label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}><span style={{ fontSize: '10px', color: '#94A3B8', fontWeight: 600 }}>Name (optional)</span>
            <input style={{ ...input, width: '160px' }} value={inv.display_name} onChange={e => setInv({ ...inv, display_name: e.target.value })} placeholder="Jane Doe" /></label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}><span style={{ fontSize: '10px', color: '#94A3B8', fontWeight: 600 }}>Role</span>
            <select style={{ ...input, width: '130px', background: 'white' }} value={inv.role} onChange={e => setInv({ ...inv, role: e.target.value as Role })}>
              {ROLES.map(r => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
            </select></label>
          <button onClick={inviteUser} disabled={busy === 'invite'}
            style={{ padding: '7px 16px', fontSize: '12px', fontWeight: 600, background: '#0891B2', color: 'white', border: 'none', borderRadius: '6px', cursor: 'pointer', height: '32px' }}>
            {busy === 'invite' ? 'Sending…' : '✉ Send invite'}
          </button>
        </div>
        <p style={{ fontSize: '11px', color: '#94A3B8', marginTop: '8px' }}>They’ll get an email with a link to set their own username, name, email and password ({ROLE_HINT[inv.role]}).</p>
        {lastInvite && (
          <div style={{ marginTop: '10px', padding: '10px 12px', background: '#F0FDF4', border: '1px solid #BBF7D0', borderRadius: '8px' }}>
            <div style={{ fontSize: '12px', color: '#334155', marginBottom: '6px' }}>
              ✓ Invite created — emailing <strong>{lastInvite.email}</strong> (ask them to check spam). Backup link to share directly:
            </div>
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
              <input readOnly value={lastInvite.url} style={{ ...input, flex: 1, fontFamily: 'monospace', fontSize: '11px', background: 'white' }} onFocus={e => e.currentTarget.select()} />
              <button onClick={() => { navigator.clipboard?.writeText(lastInvite.url); toast.success('Link copied') }}
                style={{ padding: '6px 12px', fontSize: '11px', fontWeight: 600, background: 'white', border: '1px solid #CBD5E1', borderRadius: '6px', cursor: 'pointer', color: '#475569' }}>Copy</button>
            </div>
          </div>
        )}
      </div>

      {/* Create */}
      <div style={{ marginTop: '14px', background: 'white', border: '1px solid #E2E8F0', borderRadius: '10px', padding: '14px 16px' }}>
        <div style={{ fontSize: '11px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '10px' }}>Or add a user directly</div>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}><span style={{ fontSize: '10px', color: '#94A3B8', fontWeight: 600 }}>Username</span>
            <input style={{ ...input, width: '150px' }} value={nu.username} onChange={e => setNu({ ...nu, username: e.target.value })} placeholder="jane" /></label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}><span style={{ fontSize: '10px', color: '#94A3B8', fontWeight: 600 }}>Display name</span>
            <input style={{ ...input, width: '170px' }} value={nu.display_name} onChange={e => setNu({ ...nu, display_name: e.target.value })} placeholder="Jane Doe" /></label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}><span style={{ fontSize: '10px', color: '#94A3B8', fontWeight: 600 }}>Password</span>
            <input style={{ ...input, width: '150px' }} type="text" value={nu.password} onChange={e => setNu({ ...nu, password: e.target.value })} placeholder="min 6 chars" /></label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}><span style={{ fontSize: '10px', color: '#94A3B8', fontWeight: 600 }}>Role</span>
            <select style={{ ...input, width: '130px', background: 'white' }} value={nu.role} onChange={e => setNu({ ...nu, role: e.target.value as Role })}>
              {ROLES.map(r => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
            </select></label>
          <button onClick={createUser} disabled={busy === 'new'}
            style={{ padding: '7px 16px', fontSize: '12px', fontWeight: 600, background: '#6366F1', color: 'white', border: 'none', borderRadius: '6px', cursor: 'pointer', height: '32px' }}>
            {busy === 'new' ? 'Creating…' : 'Create user'}
          </button>
        </div>
        <p style={{ fontSize: '11px', color: '#94A3B8', marginTop: '8px' }}>{ROLE_HINT[nu.role]}</p>
      </div>

      {/* Table */}
      <div style={{ marginTop: '18px', background: 'white', border: '1px solid #E2E8F0', borderRadius: '10px', overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr>
            <th style={th}>User</th><th style={th}>Role</th><th style={th}>Status</th>
            <th style={th}>Last login</th><th style={th}>Created</th><th style={th}>Actions</th>
          </tr></thead>
          <tbody>
            {loading && <tr><td style={cell} colSpan={6}>Loading…</td></tr>}
            {!loading && users.map(u => (
              <tr key={u.id} style={{ opacity: u.is_active ? 1 : 0.55 }}>
                <td style={cell}>
                  <div style={{ fontWeight: 600, color: '#0F172A' }}>{u.display_name}</div>
                  {u.email && <div style={{ color: '#64748B', fontSize: '11px' }}>{u.email}</div>}
                  <div style={{ color: '#94A3B8', fontFamily: 'monospace', fontSize: '11px' }}>
                    {u.invite_status === 'invited'
                      ? <em style={{ fontStyle: 'normal', color: '#92400E' }}>awaiting onboarding</em>
                      : <>{u.username}{u.id === me?.id ? ' · you' : ''}</>}
                  </div>
                </td>
                <td style={cell}>
                  <select value={u.role} disabled={busy === u.id} onChange={e => changeRole(u, e.target.value as Role)}
                    style={{ ...input, padding: '4px 8px', background: 'white' }}>
                    {ROLES.map(r => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
                  </select>
                </td>
                <td style={cell}>
                  {u.invite_status === 'invited' ? (
                    <span style={{ fontSize: '10px', fontWeight: 700, padding: '2px 8px', borderRadius: '99px', background: '#FEF3C7', color: '#92400E' }}>Invited</span>
                  ) : (
                    <span style={{ fontSize: '10px', fontWeight: 700, padding: '2px 8px', borderRadius: '99px', background: u.is_active ? '#DCFCE7' : '#F1F5F9', color: u.is_active ? '#166534' : '#64748B' }}>
                      {u.is_active ? 'Active' : 'Inactive'}
                    </span>
                  )}
                </td>
                <td style={cell}>{fmt(u.last_login_at)}</td>
                <td style={cell}>{fmt(u.created_at)}</td>
                <td style={cell}>
                  <div style={{ display: 'flex', gap: '6px' }}>
                    {u.invite_status === 'invited' ? (
                      <button onClick={() => resendInvite(u)} disabled={busy === u.id}
                        style={{ fontSize: '11px', padding: '3px 8px', background: 'white', border: '1px solid #BAE6FD', borderRadius: '5px', cursor: 'pointer', color: '#0369A1' }}>↻ Resend invite</button>
                    ) : (
                      <>
                        <button onClick={() => resetPassword(u)} disabled={busy === u.id}
                          style={{ fontSize: '11px', padding: '3px 8px', background: 'white', border: '1px solid #E2E8F0', borderRadius: '5px', cursor: 'pointer', color: '#475569' }}>Reset password</button>
                        <button onClick={() => toggleActive(u)} disabled={busy === u.id || u.id === me?.id}
                          style={{ fontSize: '11px', padding: '3px 8px', background: 'white', border: `1px solid ${u.is_active ? '#FECACA' : '#BBF7D0'}`, borderRadius: '5px', cursor: u.id === me?.id ? 'not-allowed' : 'pointer', color: u.is_active ? '#991B1B' : '#166534' }}>
                          {u.is_active ? 'Deactivate' : 'Reactivate'}
                        </button>
                      </>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {!loading && users.length === 0 && <tr><td style={cell} colSpan={6}>No users.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
