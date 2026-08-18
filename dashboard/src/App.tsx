import { useCallback, useEffect, useState } from 'react'
import { toast, Toaster } from 'sonner'
import { ConfigTable } from '@/components/ConfigTable'
import { ConnectionBar } from '@/components/ConnectionBar'
import { StatusPanel } from '@/components/StatusPanel'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useAdminSettings } from '@/hooks/useAdminSettings'
import { adminApi, ApiError, type ConfigResponse, type LiveKitStatus, type NgrokStatus } from '@/lib/api'

function App() {
  const { baseUrl, adminKey, update } = useAdminSettings()
  const [config, setConfig] = useState<ConfigResponse | null>(null)
  const [ngrok, setNgrok] = useState<NgrokStatus | null>(null)
  const [livekit, setLiveKit] = useState<LiveKitStatus | null>(null)
  const [connected, setConnected] = useState(false)
  const [loading, setLoading] = useState(false)
  const [refreshingStatus, setRefreshingStatus] = useState(false)

  const loadConfig = useCallback(async () => {
    if (!adminKey) {
      setConnected(false)
      return
    }
    setLoading(true)
    try {
      const data = await adminApi.getConfig(baseUrl, adminKey)
      setConfig(data)
      setConnected(true)
    } catch (err) {
      setConnected(false)
      const message = err instanceof ApiError ? err.message : 'Could not reach backend'
      toast.error(message)
    } finally {
      setLoading(false)
    }
  }, [baseUrl, adminKey])

  const loadStatus = useCallback(async () => {
    if (!adminKey) return
    setRefreshingStatus(true)
    try {
      const [ngrokStatus, livekitStatus] = await Promise.all([
        adminApi.getNgrokStatus(baseUrl, adminKey),
        adminApi.getLiveKitStatus(baseUrl, adminKey),
      ])
      setNgrok(ngrokStatus)
      setLiveKit(livekitStatus)
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'Could not reach backend'
      toast.error(message)
    } finally {
      setRefreshingStatus(false)
    }
  }, [baseUrl, adminKey])

  useEffect(() => {
    loadConfig()
    loadStatus()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseUrl, adminKey])

  const handleSave = async (key: string, value: string) => {
    try {
      await adminApi.setConfig(baseUrl, adminKey, key, value)
      toast.success(`${key} updated`)
      await loadConfig()
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'Save failed'
      toast.error(message)
      throw err
    }
  }

  const handleReset = async (key: string) => {
    try {
      await adminApi.resetConfig(baseUrl, adminKey, key)
      toast.success(`${key} reset to default`)
      await loadConfig()
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'Reset failed'
      toast.error(message)
      throw err
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <Toaster richColors position="top-right" />
      <header>
        <h1 className="text-2xl font-semibold text-foreground">Auri Dashboard</h1>
        <p className="text-sm text-muted-foreground">
          Local dev config for the LLM chain, voice masks, and the LiveKit/ngrok tunnel.
        </p>
      </header>

      <ConnectionBar
        baseUrl={baseUrl}
        adminKey={adminKey}
        connected={connected}
        onChange={update}
      />

      <Tabs defaultValue="config">
        <TabsList>
          <TabsTrigger value="config">Config</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
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
      </Tabs>
    </div>
  )
}

export default App
