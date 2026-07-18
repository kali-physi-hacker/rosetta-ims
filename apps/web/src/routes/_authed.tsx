import { createFileRoute, Outlet, redirect } from '@tanstack/react-router'
import { AppShell } from '@/components/shell/AppShell'
import { getToken } from '@/lib/auth'

// Layout route for every authenticated screen: guards on the session cookie (replaces
// the old Next.js middleware) and wraps children in the persistent app shell.
export const Route = createFileRoute('/_authed')({
  beforeLoad: () => {
    if (!getToken()) {
      throw redirect({ to: '/login' })
    }
  },
  component: () => (
    <AppShell>
      <Outlet />
    </AppShell>
  ),
})
