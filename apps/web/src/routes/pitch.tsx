import { createFileRoute, redirect } from '@tanstack/react-router'
import { getToken } from '@/lib/auth'

// Standalone pitch deck (reveal.js, static) served from /public/pitch.
// Rendered full-bleed in an iframe so it is fully isolated from the IMS app.
export const Route = createFileRoute('/pitch')({
  beforeLoad: () => {
    if (!getToken()) throw redirect({ to: '/login' })
  },
  component: PitchPage,
})

function PitchPage() {
  return (
    <iframe
      src="/pitch/index.html"
      title="Vetra Pitch Deck"
      style={{
        position: 'fixed',
        inset: 0,
        width: '100vw',
        height: '100vh',
        border: 'none',
      }}
    />
  )
}
