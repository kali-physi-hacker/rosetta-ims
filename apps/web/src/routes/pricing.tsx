import { createFileRoute, redirect } from '@tanstack/react-router'

// Retired: the Pricing & GP Matrix has been folded into All Inventory — open it there via the
// "Margins" toggle. This redirect keeps old links working (including the SKU detail view's
// "View Pricing Matrix" button) and lands on the margin column view.
export const Route = createFileRoute('/pricing')({
  beforeLoad: () => {
    throw redirect({ to: '/', search: { view: 'margins' } as never })
  },
})
