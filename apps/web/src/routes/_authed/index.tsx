import { createFileRoute } from '@tanstack/react-router'
import { PendingRoute } from '@/components/PendingRoute'

// `/` — the All Inventory master grid (1,534 lines in the old app: NDJSON streaming,
// CSV export, margins view). Scheduled as a dedicated port — see MIGRATION.md.
export const Route = createFileRoute('/_authed/')({
  component: () => (
    <PendingRoute title="All Inventory" note="The master inventory grid is scheduled as a dedicated port." />
  ),
})
