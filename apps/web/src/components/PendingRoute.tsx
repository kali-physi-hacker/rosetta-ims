import { C } from '@/lib/tokens'
// Temporary marker for screens not yet ported from the old app. Every use of this
// disappears as MIGRATION.md is worked through — it is not part of the final UI.
export function PendingRoute({ title, note }: { title: string; note?: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '60vh', padding: '24px' }}>
      <div style={{ maxWidth: '440px', textAlign: 'center' }}>
        <div style={{ fontSize: '11px', fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: C.faint }}>
          Migration in progress
        </div>
        <h1 style={{ fontSize: '20px', fontWeight: 700, color: C.ink, marginTop: '8px' }}>{title}</h1>
        <p style={{ fontSize: '13px', color: C.muted, lineHeight: 1.5, marginTop: '8px' }}>
          {note ?? "This screen hasn't been ported to the new app yet."} See MIGRATION.md for status.
        </p>
      </div>
    </div>
  )
}
