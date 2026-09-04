import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { useAuth } from '@/hooks/useAuth'
import { adminApi, ApiError, type BuildStatus } from '@/lib/api'

interface BuildPanelProps {
  /** Pre-fills the backend-URL field — the live ngrok URL if one's running. */
  suggestedBackendUrl: string | null
}

const POLL_INTERVAL_MS = 2000

const STATUS_VARIANT: Record<BuildStatus['status'], 'default' | 'destructive' | 'outline'> = {
  idle: 'outline',
  running: 'default',
  success: 'default',
  failed: 'destructive',
}

/** Trigger a local `gradlew assembleRelease` with a given backend URL baked
 * in, and poll its status/log until it finishes. */
export function BuildPanel({ suggestedBackendUrl }: BuildPanelProps) {
  const { authedRequest } = useAuth()
  const [backendUrlDraft, setBackendUrlDraft] = useState('')
  const [build, setBuild] = useState<BuildStatus | null>(null)
  const [starting, setStarting] = useState(false)
  const logRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (suggestedBackendUrl && !backendUrlDraft) {
      setBackendUrlDraft(suggestedBackendUrl)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [suggestedBackendUrl])

  // Pick up an already-running or just-finished build on mount/reconnect —
  // without this, reloading the page (or just opening this tab) shows a
  // blank panel even though the backend has real build state to report.
  useEffect(() => {
    adminApi
      .getBuildStatus(authedRequest)
      .then((s) => setBuild((prev) => prev ?? s))
      .catch(() => {
        // No prior build state (or backend unreachable) — leave the panel blank, same as before.
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authedRequest])

  useEffect(() => {
    if (build?.status !== 'running') return
    const id = setInterval(async () => {
      try {
        const status = await adminApi.getBuildStatus(authedRequest)
        setBuild(status)
      } catch {
        // Transient poll failure — next tick retries; don't spam toasts.
      }
    }, POLL_INTERVAL_MS)
    return () => clearInterval(id)
  }, [authedRequest, build?.status])

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight })
  }, [build?.log])

  const handleStart = async () => {
    if (!backendUrlDraft.trim()) {
      toast.error('Enter a backend URL first')
      return
    }
    setStarting(true)
    try {
      const status = await adminApi.startBuild(authedRequest, backendUrlDraft.trim())
      setBuild(status)
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'Could not start build'
      toast.error(message)
    } finally {
      setStarting(false)
    }
  }

  const isRunning = build?.status === 'running'

  return (
    <div className="space-y-4 rounded-lg border bg-card p-4">
      <div>
        <h3 className="font-medium text-card-foreground">Build APK</h3>
        <p className="text-sm text-muted-foreground">
          Runs <code className="font-mono">gradlew assembleRelease</code> locally with{' '}
          <code className="font-mono">EXPO_PUBLIC_API_URL</code> set to the URL below — one APK,
          repoint it anytime from the app's own Settings screen (no rebuild needed for future URL
          changes).
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-4">
        <div className="grid gap-1.5">
          <Label htmlFor="build-backend-url">Backend URL to bake in</Label>
          <Input
            id="build-backend-url"
            value={backendUrlDraft}
            onChange={(e) => setBackendUrlDraft(e.target.value)}
            placeholder="https://your-tunnel.ngrok-free.dev"
            className="w-80 font-mono text-xs"
            disabled={isRunning}
          />
        </div>
        <Button onClick={handleStart} disabled={isRunning || starting}>
          {isRunning ? 'Building…' : 'Build APK'}
        </Button>
        {build && (
          <Badge variant={STATUS_VARIANT[build.status]}>{build.status}</Badge>
        )}
      </div>

      {build && (
        <>
          {build.status === 'success' && build.apk_path && (
            <p className="font-mono text-xs text-muted-foreground">
              APK: {build.apk_path}
            </p>
          )}
          <Textarea
            ref={logRef}
            readOnly
            value={build.log}
            className="h-64 resize-none font-mono text-xs"
            placeholder="Build output will appear here…"
          />
        </>
      )}
    </div>
  )
}
