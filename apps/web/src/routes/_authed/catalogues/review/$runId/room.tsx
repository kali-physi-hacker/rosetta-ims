// DEPRECATED — the exception room folded into the run desk's focus overlay
// (rev-04). Old links and bookmarks land on the desk.
import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/_authed/catalogues/review/$runId/room')({
  beforeLoad: ({ params }) => {
    throw redirect({ to: '/catalogues/review/$runId', params: { runId: params.runId }, replace: true })
  },
})
