import { useCallback, useEffect, useState } from 'react'
import { toast, Toaster } from 'sonner'
import { BuildPanel } from '@/components/BuildPanel'
import { ConfigTable } from '@/components/ConfigTable'
import { AuditPanel } from '@/components/AuditPanel'
import { ConnectionBar } from '@/components/ConnectionBar'
import { DeliveryPanel } from '@/components/DeliveryPanel'
import { DirectoryPanel } from '@/components/DirectoryPanel'
import { InsightsPanel } from '@/components/InsightsPanel'
import { PrivacyPanel } from '@/components/PrivacyPanel'
import { QueuePanel } from '@/components/QueuePanel'
import { RepliesPanel } from '@/components/RepliesPanel'
import { LoginScreen } from '@/components/LoginScreen'
import { StatusPanel } from '@/components/StatusPanel'
import { ThemesPanel } from '@/components/ThemesPanel'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useAuth } from '@/hooks/useAuth'
import {
  adminApi,
  ApiError,
  type ConfigResponse,
  type LiveKitStatus,
  type NgrokStatus,
  type UserRole,
} from '@/lib/api'

/**
 * Which roles may see which tab.
 *
 * The Phase 10 developer tabs are admin-only: an HR lead has no business
 * rotating LLM API keys or building an APK, and a moderator less still.
 * Later Phase 11 tabs register here as they land.
 */
const TAB_ACCESS: { value: string; label: string; roles: UserRole[] }[] = [
  { value: 'insights', label: 'Insights', roles: ['hr'] },
  { value: 'queue', label: 'Queue', roles: ['hr', 'moderator'] },
  { value: 'directory', label: 'Directory', roles: ['hr'] },
  { value: 'delivery', label: 'Delivery', roles: ['hr'] },
  { value: 'replies', label: 'Replies', roles: ['hr'] },
  { value: 'themes', label: 'Themes', roles: ['hr'] },
  { value: 'privacy', label: 'Privacy', roles: ['hr', 'admin'] },
  { value: 'config', label: 'Config', roles: ['admin'] },
  { value: 'status', label: 'Status', roles: ['admin'] },
  { value: 'build', label: 'Build', roles: ['admin'] },
  { value: 'audit', label: 'Audit', roles: ['admin'] },
]

function App() {
  const { baseUrl, adminKey, updateConnection, user, signedIn, logout, authedRequest } = useAuth()
  const [config, setConfig] = useState<ConfigResponse | null>(null)
  const [ngrok, setNgrok] = useState<NgrokStatus | null>(null)
  const [livekit, setLiveKit] = useState<LiveKitStatus | null>(null)
  const [connected, setConnected] = useState(false)
  const [loading, setLoading] = useState(false)
  const [refreshingStatus, setRefreshingStatus] = useState(false)

  // The legacy shared secret keeps working as an admin escape hatch, so the
  // dashboard is reachable either by signing in or by pasting that key.
  const usingLegacyKey = !signedIn && adminKey !== ''
  const authorized = signedIn || usingLegacyKey
  const visibleTabs = TAB_ACCESS.filter(
    (tab) => usingLegacyKey || (user !== null && tab.roles.includes(user.role)),
  )
  // Only the admin/dev panels talk to /admin/*; an HR session must not fire
  // those requests just because it can see the Insights tab.
  const canSeeDevTabs = visibleTabs.some((tab) => tab.value === 'config')

  const loadConfig = useCallback(async () => {
    if (!authorized || !canSeeDevTabs) return
    setLoading(true)
    try {
      setConfig(await adminApi.getConfig(authedRequest))
      setConnected(true)
    } catch (err) {
      setConnected(false)
      toast.error(err instanceof ApiError ? err.message : 'Could not reach backend')
    } finally {
      setLoading(false)
    }
  }, [authorized, canSeeDevTabs, authedRequest])

  const loadStatus = useCallback(async () => {
    if (!authorized || !canSeeDevTabs) return
    setRefreshingStatus(true)
    try {
      const [ngrokStatus, livekitStatus] = await Promise.all([
        adminApi.getNgrokStatus(authedRequest),
        adminApi.getLiveKitStatus(authedRequest),
      ])
      setNgrok(ngrokStatus)
      setLiveKit(livekitStatus)
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not reach backend')
    } finally {
      setRefreshingStatus(false)
    }
  }, [authorized, canSeeDevTabs, authedRequest])

  useEffect(() => {
    loadConfig()
    loadStatus()
  }, [loadConfig, loadStatus])

  const handleSave = async (key: string, value: string) => {
    try {
      await adminApi.setConfig(authedRequest, key, value)
      toast.success(`${key} updated`)
      await loadConfig()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Save failed')
      throw err
    }
  }

  const handleReset = async (key: string) => {
    try {
      await adminApi.resetConfig(authedRequest, key)
      toast.success(`${key} reset to default`)
      await loadConfig()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Reset failed')
      throw err
    }
  }

  if (!authorized) {
    return (
      <>
        <Toaster richColors position="top-right" />
        <LoginScreen />
      </>
    )
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <Toaster richColors position="top-right" />
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-foreground">Auri Dashboard</h1>
          <p className="text-sm text-muted-foreground">
            {user
              ? `Signed in as ${user.email} (${user.role})`
              : 'Signed in with the legacy admin key'}
          </p>
        </div>
        {signedIn && (
          <Button variant="outline" onClick={logout}>
            Sign out
          </Button>
        )}
      </header>

      <ConnectionBar
        baseUrl={baseUrl}
        adminKey={adminKey}
        connected={connected}
        onChange={updateConnection}
      />

      {visibleTabs.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Your role has no panels available yet.
        </p>
      ) : (
        <Tabs defaultValue={visibleTabs[0].value}>
          <TabsList>
            {visibleTabs.map((tab) => (
              <TabsTrigger key={tab.value} value={tab.value}>
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>

          <TabsContent value="config" className="space-y-6">
            {loading || !config ? (
              <div className="space-y-4">
                <Skeleton className="h-40 w-full" />
                <Skeleton className="h-40 w-full" />
              </div>
            ) : (
              <>
                <ConfigTable
                  title="LLM Provider Chain"
                  description="Ollama → Gemini → OpenAI (auto chain); Claude is explicit-provider-only."
                  entries={config.llm}
                  onSave={handleSave}
                  onReset={handleReset}
                />
                <ConfigTable
                  title="Speech-to-Text"
                  description="faster-whisper model size, with OpenAI Whisper API fallback."
                  entries={config.stt}
                  onSave={handleSave}
                  onReset={handleReset}
                />
                <ConfigTable
                  title="HR Analytics"
                  description="Smallest bucket size an aggregate may report; smaller cohorts are suppressed."
                  entries={config.analytics}
                  onSave={handleSave}
                  onReset={handleReset}
                />
                <ConfigTable
                  title="Voice Masks"
                  description="SoX effect chain per mask, as a JSON list of arguments."
                  entries={config.voice_masks}
                  onSave={handleSave}
                  onReset={handleReset}
                />
              </>
            )}
          </TabsContent>

          <TabsContent value="status">
            <StatusPanel
              ngrok={ngrok}
              livekit={livekit}
              onRefresh={loadStatus}
              refreshing={refreshingStatus}
            />
          </TabsContent>

          <TabsContent value="build" className="space-y-6">
            <BuildPanel suggestedBackendUrl={ngrok?.public_url ?? null} />
            {config && (
              <ConfigTable
                title="Build Settings"
                description="Local build-tool overrides."
                entries={config.build}
                onSave={handleSave}
                onReset={handleReset}
              />
            )}
          </TabsContent>
          <TabsContent value="insights">
            <InsightsPanel />
          </TabsContent>

          <TabsContent value="queue">
            <QueuePanel />
          </TabsContent>

          <TabsContent value="directory">
            <DirectoryPanel />
          </TabsContent>

          <TabsContent value="delivery">
            <DeliveryPanel />
          </TabsContent>

          <TabsContent value="replies">
            <RepliesPanel />
          </TabsContent>

          <TabsContent value="themes">
            <ThemesPanel />
          </TabsContent>

          <TabsContent value="privacy">
            <PrivacyPanel />
          </TabsContent>

          <TabsContent value="audit">
            <AuditPanel />
          </TabsContent>
        </Tabs>
      )}
    </div>
  )
}

export default App
