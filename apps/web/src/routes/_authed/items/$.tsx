// DEPRECATED — the classic SKU details page. The instrument view at
// /sku/<code> is the main UI now; this route only forwards old links and
// bookmarks there. The full classic page is preserved verbatim next to this
// file as `$.tsx.deprecated` — restore it by renaming it back over this file.
import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/_authed/items/$')({
  beforeLoad: ({ params }) => {
    throw redirect({ to: '/sku/$' as never, params: { _splat: params._splat } as never, replace: true })
  },
})
