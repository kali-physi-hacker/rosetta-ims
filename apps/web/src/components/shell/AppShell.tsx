import { Sidebar } from './Sidebar'
import { Toaster } from '@/components/Toaster'
import { ConfirmDialog } from '@/components/ConfirmDialog'

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>
      <Sidebar />
      <main style={{ flex: 1, height: '100vh', padding: '20px 22px', overflow: 'auto', background: '#F6F7F9' }}>
        {children}
      </main>
      <Toaster />
      <ConfirmDialog />
    </div>
  )
}
