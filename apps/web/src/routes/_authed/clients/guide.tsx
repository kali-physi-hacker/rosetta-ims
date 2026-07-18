import { createFileRoute } from '@tanstack/react-router'
import { GuideContent } from '@/components/GuideContent'

export const Route = createFileRoute('/_authed/clients/guide')({ component: GuidePage })

function GuidePage() {
  return <GuideContent />
}
