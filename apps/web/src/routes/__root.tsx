import { createRootRouteWithContext, Outlet } from '@tanstack/react-router'
import type { QueryClient } from '@tanstack/react-query'
import { PendingRoute } from '@/components/PendingRoute'

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  component: () => <Outlet />,
  notFoundComponent: () => (
    <PendingRoute title="Not migrated yet" note="This screen hasn't been ported to the new app yet." />
  ),
})
