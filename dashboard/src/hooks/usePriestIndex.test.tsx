import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { usePriestIndex } from '@/hooks/usePriestIndex'
import { ApiError } from '@/lib/api'
import { IDLE_STATUS, INDEX, makeRequester, status, type FakeRequester } from '@/test/priestFixtures'

const auth = vi.hoisted(() => ({ request: null as unknown }))
vi.mock('@/hooks/useAuth', () => ({ useAuth: () => ({ authedRequest: auth.request }) }))

const INDEX_KEY = 'GET /admin/priest/index'
const STATUS_KEY = 'GET /admin/priest/reindex/status'
const START_KEY = 'POST /admin/priest/reindex'
const ACTIVATE_KEY = 'POST /admin/priest/activate'

let request: FakeRequester

function setup() {
  request = makeRequester({
    [INDEX_KEY]: INDEX,
    [STATUS_KEY]: IDLE_STATUS,
    [START_KEY]: { state: 'running', started_at: '2026-09-30T10:00:00Z' },
    [ACTIVATE_KEY]: { active_version: 'v1' },
  })
  auth.request = request
  return renderHook(() => usePriestIndex())
}

async function tick(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

describe('usePriestIndex', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('loads the index and the last build status on mount', async () => {
    // Arrange / Act
    const { result } = setup()
    await tick(0)

    // Assert
    expect(result.current.index).toEqual({ status: 'ready', data: INDEX })
    expect(result.current.reindex).toEqual(IDLE_STATUS)
  })

  it('reports an error when the index cannot be loaded', async () => {
    // Arrange
    request = makeRequester({ [INDEX_KEY]: new ApiError('nope', 503), [STATUS_KEY]: IDLE_STATUS })
    auth.request = request

    // Act
    const { result } = renderHook(() => usePriestIndex())
    await tick(0)

    // Assert
    expect(result.current.index).toEqual({ status: 'error', message: 'nope' })
  })

  it('does not poll while nothing is running', async () => {
    // Arrange
    setup()
    await tick(0)

    // Act
    await tick(6000)

    // Assert
    expect(request.callsTo(STATUS_KEY)).toHaveLength(1)
  })

  it('polls every two seconds after a reindex starts and reloads the index when it succeeds', async () => {
    // Arrange
    const { result } = setup()
    await tick(0)
    request.routes[STATUS_KEY] = status({ state: 'running', notes_total: 10, notes_indexed: 3 })

    // Act
    await act(async () => {
      await result.current.startReindex()
    })
    await tick(2000)
    const runningPolls = request.callsTo(STATUS_KEY).length
    request.routes[STATUS_KEY] = status({ state: 'succeeded', notes_total: 10, notes_indexed: 10 })
    await tick(2000)
    const indexLoadsAfterFinish = request.callsTo(INDEX_KEY).length
    await tick(6000)

    // Assert
    expect(request.callsTo(START_KEY)).toHaveLength(1)
    expect(runningPolls).toBeGreaterThanOrEqual(2)
    expect(result.current.reindex?.state).toBe('succeeded')
    expect(indexLoadsAfterFinish).toBe(2)
    expect(request.callsTo(STATUS_KEY).length).toBe(runningPolls + 1)
  })

  it('treats 409 already_running as a build in progress, not an error', async () => {
    // Arrange
    const { result } = setup()
    await tick(0)
    request.routes[START_KEY] = new ApiError('already_running', 409)
    request.routes[STATUS_KEY] = status({ state: 'running' })

    // Act
    await act(async () => {
      await result.current.startReindex()
    })

    // Assert
    expect(result.current.reindex?.state).toBe('running')
    expect(result.current.index.status).toBe('ready')
  })

  it('activates a version by name and reloads the index', async () => {
    // Arrange
    const { result } = setup()
    await tick(0)

    // Act
    await act(async () => {
      await result.current.activate('v1')
    })

    // Assert
    expect(request.callsTo(ACTIVATE_KEY)[0].body).toEqual({ version: 'v1' })
    await tick(0)
    expect(request.callsTo(INDEX_KEY)).toHaveLength(2)
  })

  it('keeps the current index when an activation fails', async () => {
    // Arrange
    const { result } = setup()
    await tick(0)
    request.routes[ACTIVATE_KEY] = new ApiError('unknown version', 404)

    // Act
    await act(async () => {
      await result.current.activate('ghost')
    })

    // Assert
    expect(result.current.index).toEqual({ status: 'ready', data: INDEX })
    expect(request.callsTo(INDEX_KEY)).toHaveLength(1)
  })
})
