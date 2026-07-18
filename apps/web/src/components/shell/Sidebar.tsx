import { Link, useLocation } from '@tanstack/react-router'
import { useEffect, useState, type ReactNode } from 'react'
import { getUser, logout, CAPABILITIES, ROLE_LABELS, type IMSUser } from '@/lib/auth'

// Line-icon set matching the redesign (20×20 viewBox, currentColor stroke).
const ICON_PATHS: Record<string, ReactNode> = {
  doc:    <><path d="M5 2h7l3 3v13H5z" /><path d="M12 2v3h3M7 10h6M7 13h6" /></>,
  arch:   <><rect x="3" y="12" width="5" height="5" /><rect x="12" y="12" width="5" height="5" /><rect x="7.5" y="3" width="5" height="5" /><path d="M10 8v3M6 12v-1h8v1" /></>,
  play:   <><circle cx="10" cy="10" r="7" /><path d="M8 7l5 3-5 3z" fill="currentColor" stroke="none" /></>,
  stack:  <><path d="M10 3l7 4-7 4-7-4z" /><path d="M3 11l7 4 7-4" /></>,
  box:    <><rect x="3" y="3" width="14" height="14" rx="2" /><path d="M3 8h14M8 8v9" /></>,
  grid:   <><rect x="3" y="3" width="6" height="6" rx="1" /><rect x="11" y="3" width="6" height="6" rx="1" /><rect x="3" y="11" width="6" height="6" rx="1" /><rect x="11" y="11" width="6" height="6" rx="1" /></>,
  client: <><circle cx="7" cy="7" r="3" /><path d="M2 17c0-3 2.5-5 5-5s5 2 5 5M13 5a3 3 0 010 6M18 17c0-2.5-1.5-4-4-4.5" /></>,
  check:  <><circle cx="10" cy="10" r="7" /><path d="M7 10l2 2 4-4" /></>,
  upload: <path d="M10 13V4M6 8l4-4 4 4M4 15h12" />,
  layers: <path d="M10 3l7 4-7 4-7-4zM3 11l7 4 7-4M3 14l7 4 7-4" />,
  refresh: <><path d="M16 10a6 6 0 11-1.8-4.3" /><path d="M16 3.5V7h-3.5" /></>,
  list:   <path d="M7 5h10M7 10h10M7 15h10M3.5 5h.01M3.5 10h.01M3.5 15h.01" />,
  truck:  <><path d="M2 5h10v8H2zM12 8h4l2 2v3h-6" /><circle cx="6" cy="15" r="1.5" /><circle cx="15" cy="15" r="1.5" /></>,
  user:   <><circle cx="10" cy="7" r="3" /><path d="M4 17c0-3.3 2.7-6 6-6s6 2.7 6 6" /></>,
  clock:  <><circle cx="10" cy="10" r="7" /><path d="M10 6v4l3 2" /></>,
}
function Icon({ name }: { name: string }) {
  return <svg width="17" height="17" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5">{ICON_PATHS[name] ?? ICON_PATHS.doc}</svg>
}

// Nav targets are data-driven over routes that are still being migrated, so `to`
// can't be statically type-checked against the route tree yet. This cast is the one
// place that acknowledges that; it goes away once every route exists (see MIGRATION.md).
function NavLink({ to, className, title, children }: { to: string; className?: string; title?: string; children: ReactNode }) {
  return <Link to={to as never} className={className} title={title}>{children}</Link>
}

type NavItem = { href: string; label: string; icon: string; badge?: string }

const NAV: NavItem[] = [
  { href: '/playbook',       label: 'Read Me First',  icon: 'doc' },
  { href: '/architecture',   label: 'Architecture',   icon: 'arch' },
  { href: '/ssot-spec',      label: 'SSOT Spec',      icon: 'doc' },
  { href: '/am-walkthrough', label: 'AM Walkthrough', icon: 'play' },
  { href: '/tech-stack',     label: 'Tech Stack',     icon: 'stack' },
  { href: '/',               label: 'All Inventory',  icon: 'box' },
  { href: '/collections',    label: 'Collections',    icon: 'grid' },
]
const NAV_CLIENTS: NavItem[] = [{ href: '/clients', label: 'Clientbase', icon: 'client' }]
const NAV_OPS: NavItem[] = [
  { href: '/data-review', label: 'Data Review',  icon: 'check' },
  { href: '/stock',       label: 'Stock Import', icon: 'upload' },
  { href: '/catalogues',  label: 'Catalogues',   icon: 'layers' },
  { href: '/catalogues/reparse', label: 'Re-parse', icon: 'refresh' },
]
const NAV_ADMIN: NavItem[] = [
  { href: '/categories', label: 'Categories', icon: 'list' },
  { href: '/suppliers',  label: 'Suppliers',  icon: 'truck' },
]
const NAV_ADMIN_PANEL: NavItem[] = [
  { href: '/admin/users', label: 'Users',            icon: 'user' },
  { href: '/admin/audit', label: 'Audit Log',        icon: 'clock' },
  { href: '/config',      label: 'Transform Config', icon: 'stack' },
]

const ROLE_AVATAR_BG: Record<string, string> = { admin: '#6366F1', bizops: '#0891B2', data_entry: '#0D9488' }
function initials(name: string): string { return name.split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2) }

function NavSection({ items, label, pathname }: { items: NavItem[]; label: string; pathname: string }) {
  return (
    <div className="navsec">
      <div className="navlbl">{label}</div>
      {items.map(({ href, label, icon, badge }) => {
        const active = href === '/' ? pathname === '/' : pathname.startsWith(href)
        return (
          <NavLink key={href} to={href} className={active ? 'on' : ''} title={label}>
            <Icon name={icon} />
            <span className="lbl">{label}</span>
            {badge && <span className="nav-badge lbl">{badge}</span>}
          </NavLink>
        )
      })}
    </div>
  )
}

const SIDE_CSS = `
.side{width:230px;background:#0F172A;color:#94A3B8;flex:none;display:flex;flex-direction:column;transition:width .2s cubic-bezier(.4,0,.2,1);height:100vh;overflow-y:auto;overflow-x:hidden}
.side.collapsed{width:64px}
.side .side-top{display:flex;align-items:center;gap:10px;padding:15px 15px 12px;flex:none}
.side .logo{width:30px;height:30px;border-radius:8px;background:linear-gradient(135deg,#4F46E5,#7C74F0);flex:none;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:800;font-size:14px}
.side .brandwrap{white-space:nowrap;overflow:hidden}
.side .brandwrap .b1{font-weight:700;color:#fff;font-size:15px;line-height:1.15;letter-spacing:-0.3px}
.side .brandwrap .b1 .etta{color:#818CF8;font-style:normal}
.side .brandwrap .b1 .ims{color:#94A3B8;font-size:11px;font-weight:600;margin-left:2px}
.side .brandwrap .b2{font-size:8.5px;letter-spacing:.09em;color:#64748B;text-transform:uppercase}
.side.collapsed .brandwrap{opacity:0}
.side .nav{flex:1;min-height:0;padding-bottom:6px}
.side .navsec{padding:2px 8px}
.side .navlbl{font-size:9.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#475569;padding:10px 11px 4px;white-space:nowrap}
.side.collapsed .navlbl{visibility:hidden;height:8px;padding:4px 0}
.side .nav a{display:flex;align-items:center;gap:11px;padding:8px 11px;border-radius:8px;color:#94A3B8;text-decoration:none;font-size:12.5px;font-weight:500;white-space:nowrap;margin:1px 0}
.side .nav a:hover{background:#1E293B;color:#E2E8F0}
.side .nav a.on{background:#1E3A5F;color:#93C5FD;font-weight:600}
.side .nav a svg{flex:none;width:17px;height:17px}
.side.collapsed .nav a{justify-content:center;padding:8px}
.side .nav-badge{background:#7F1D1D;color:#FCA5A5;font-size:10px;font-weight:600;padding:1px 6px;border-radius:10px;margin-left:auto}
.side .side-foot{padding:10px 8px;border-top:1px solid #1E293B;flex:none;display:flex;flex-direction:column;gap:6px}
.side .foot-link{display:flex;align-items:center;gap:11px;padding:7px 11px;border-radius:8px;color:#475569;text-decoration:none;font-size:11px;white-space:nowrap}
.side .foot-link:hover{background:#1E293B;color:#E2E8F0}
.side .foot-link .ext{width:17px;text-align:center;flex:none}
.side .userbox{display:flex;align-items:center;gap:9px;padding:4px 6px}
.side .avatar{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;color:#fff;flex:none}
.side .uinfo{min-width:0;flex:1}
.side .uinfo .uname{font-size:12px;font-weight:600;color:#E2E8F0;margin:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.side .uinfo .urole{font-size:10px;color:#475569;margin:0}
.side .signout{background:none;border:none;color:#475569;font-size:11px;cursor:pointer;padding:0;flex:none;font-family:inherit}
.side .signout:hover{color:#F87171}
.side .side-btm{padding:10px 8px;border-top:1px solid #1E293B;flex:none}
.side .collapse-btn{display:flex;align-items:center;gap:11px;width:100%;padding:9px 11px;border-radius:8px;background:none;border:none;color:#94A3B8;font-family:inherit;font-size:12px;font-weight:600;cursor:pointer;white-space:nowrap}
.side .collapse-btn:hover{background:#1E293B;color:#E2E8F0}
.side .collapse-btn svg{flex:none;transition:transform .2s}
.side.collapsed .collapse-btn{justify-content:center}
.side.collapsed .collapse-btn svg{transform:rotate(180deg)}
.side.collapsed .lbl{display:none}
.side.collapsed .side-foot,.side.collapsed .userbox,.side.collapsed .foot-link{align-items:center;justify-content:center;padding-left:0;padding-right:0}
`

export function Sidebar() {
  const pathname = useLocation({ select: (l) => l.pathname })
  const [user, setUser] = useState<IMSUser | null>(null)
  const [collapsed, setCollapsed] = useState(false)

  useEffect(() => {
    setUser(getUser())
    try { setCollapsed(localStorage.getItem('ims_sidebar_collapsed') === '1') } catch {}
  }, [])
  const toggle = () => setCollapsed(v => {
    const n = !v
    try { localStorage.setItem('ims_sidebar_collapsed', n ? '1' : '0') } catch {}
    return n
  })

  const has = (cap: string) => !!user && (CAPABILITIES[cap]?.includes(user.role) ?? false)
  const ops = NAV_OPS.filter(i =>
    (i.href !== '/stock' || has('stock_import')) &&
    (i.href !== '/catalogues/reparse' || has('catalogue_onboard')))

  return (
    <>
      <style>{SIDE_CSS}</style>
      <aside className={`side ${collapsed ? 'collapsed' : ''}`}>
        <div className="side-top">
          <div className="logo">R</div>
          <div className="brandwrap"><div className="b1">ros<em className="etta">etta</em> <span className="ims">IMS</span></div><div className="b2">Inventory Management</div></div>
        </div>

        <nav className="nav">
          <NavSection items={NAV}         label="Inventory"   pathname={pathname} />
          <NavSection items={NAV_CLIENTS} label="Client SSOT" pathname={pathname} />
          {/* Stock Import is Admin-only; Data Review + Catalogues are for everyone. */}
          <NavSection items={ops}         label="Operations"  pathname={pathname} />
          <NavSection items={NAV_ADMIN}   label="Reference"   pathname={pathname} />
          {has('user_admin') && <NavSection items={NAV_ADMIN_PANEL} label="Admin" pathname={pathname} />}
        </nav>

        <div className="side-foot">
          <a href="http://localhost:3000" className="foot-link" title="Open Rosetta">
            <span className="ext">↗</span><span className="lbl">Open Rosetta</span>
          </a>
          {user ? (
            <div className="userbox">
              <div className="avatar" style={{ background: ROLE_AVATAR_BG[user.role] ?? '#0891B2' }} title={user.display_name}>{initials(user.display_name)}</div>
              <div className="uinfo lbl">
                <p className="uname">{user.display_name}</p>
                <p className="urole">{ROLE_LABELS[user.role] ?? user.role}</p>
              </div>
              <button onClick={logout} className="signout lbl" title="Sign out">Sign out</button>
            </div>
          ) : (
            <div className="userbox"><div className="avatar" style={{ background: '#1E293B' }} /></div>
          )}
        </div>

        <div className="side-btm">
          <button className="collapse-btn" onClick={toggle} title={collapsed ? 'Expand menu' : 'Collapse menu'}>
            <svg width="17" height="17" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M12 5l-5 5 5 5" strokeLinecap="round" strokeLinejoin="round" /></svg>
            <span className="lbl">Collapse menu</span>
          </button>
        </div>
      </aside>
    </>
  )
}
