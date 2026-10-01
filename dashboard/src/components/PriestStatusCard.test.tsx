import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PriestStatusCard } from '@/components/PriestStatusCard'
import { ApiError } from '@/lib/api'
import {
  configResponse,
  entry,
  HEALTH,
  makeRequester,
  PRIEST_CONFIG,
  type FakeRequester,
} from '@/test/priestFixtures'

const auth = vi.hoisted(() => ({ request: null as unknown }))
vi.mock('@/hooks/useAuth', () => ({ useAuth: () => ({ authedRequest: auth.request }) }))

const CONFIG_GET = 'GET /admin/config'
const CONFIG_PUT = 'PUT /admin/config'
const HEALTH_GET = 'GET /admin/priest/health'

let request: FakeRequester

function setup(routes: Record<string, unknown> = {}) {
  request = makeRequester({
    [CONFIG_GET]: configResponse(),
    [CONFIG_PUT]: entry('PRIEST_MODE_ENABLED', 'true', 'db'),
    [HEALTH_GET]: HEALTH,
    ...routes,
  })
  auth.request = request
  return render(<PriestStatusCard />)
}

function killSwitch(): HTMLElement {
  return screen.getByRole('switch', { name: /guide is/i })
}

describe('PriestStatusCard', () => {
  beforeEach(() => {
    auth.request = null
  })

  it('shows a loading skeleton before anything has arrived', () => {
    // Arrange / Act
    setup()

    // Assert
    expect(screen.getByRole('status', { name: /loading the guide status/i })).toBeTruthy()
  })

  it('shows the persona, the models and each endpoint as reachable or not', async () => {
    // Arrange / Act
    setup()
    await screen.findByText('qwen-test')

    // Assert
    expect(screen.getByText('llama-test')).toBeTruthy()
    expect(screen.getByText('Guide', { selector: 'dd' })).toBeTruthy()
    expect(screen.getByText(/primary \(vllm\): reachable/i)).toBeTruthy()
    expect(screen.getByText(/fallback \(ollama\): unreachable/i)).toBeTruthy()
    expect(screen.getByText(/embedder: reachable/i)).toBeTruthy()
  })

  it('says so when no fallback is configured', async () => {
    // Arrange / Act
    setup({
      [HEALTH_GET]: {
        ...HEALTH,
        fallback: { configured: false, reachable: null, kind: 'ollama', host: '' },
      },
      [CONFIG_GET]: configResponse([...PRIEST_CONFIG.slice(0, 3), entry('PRIEST_FALLBACK_MODEL', '')]),
    })

    // Assert
    expect(await screen.findByText(/fallback: not configured/i)).toBeTruthy()
    expect(screen.getByText('None set')).toBeTruthy()
  })

  it('asks before turning the Guide on and only then saves the setting', async () => {
    // Arrange
    setup()
    await screen.findByText('qwen-test')

    // Act
    fireEvent.click(killSwitch())
    const dialog = await screen.findByRole('alertdialog')
    const savedBeforeConfirm = request.callsTo(CONFIG_PUT).length
    fireEvent.click(within(dialog).getByRole('button', { name: /turn on/i }))

    // Assert
    expect(within(dialog).getByText(/turn the guide on/i)).toBeTruthy()
    expect(savedBeforeConfirm).toBe(0)
    await waitFor(() => expect(request.callsTo(CONFIG_PUT)).toHaveLength(1))
    expect(request.callsTo(CONFIG_PUT)[0].body).toEqual({ key: 'PRIEST_MODE_ENABLED', value: 'true' })
  })

  it('does nothing when the confirmation is cancelled', async () => {
    // Arrange
    setup()
    await screen.findByText('qwen-test')

    // Act
    fireEvent.click(killSwitch())
    const dialog = await screen.findByRole('alertdialog')
    fireEvent.click(within(dialog).getByRole('button', { name: /cancel/i }))

    // Assert
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(request.callsTo(CONFIG_PUT)).toHaveLength(0)
    expect(killSwitch().getAttribute('aria-checked')).toBe('false')
  })

  it('asks before turning the Guide off and saves false', async () => {
    // Arrange
    setup({
      [CONFIG_GET]: configResponse([entry('PRIEST_MODE_ENABLED', 'true', 'db'), ...PRIEST_CONFIG.slice(1)]),
    })
    await screen.findByText('qwen-test')

    // Act
    fireEvent.click(killSwitch())
    const dialog = await screen.findByRole('alertdialog')
    fireEvent.click(within(dialog).getByRole('button', { name: /turn off/i }))

    // Assert
    expect(killSwitch().getAttribute('aria-checked')).toBe('true')
    await waitFor(() => expect(request.callsTo(CONFIG_PUT)).toHaveLength(1))
    expect(request.callsTo(CONFIG_PUT)[0].body).toEqual({ key: 'PRIEST_MODE_ENABLED', value: 'false' })
  })

  it('reflects the saved value after a confirmed change', async () => {
    // Arrange
    setup()
    await screen.findByText('qwen-test')
    request.routes[CONFIG_GET] = configResponse([entry('PRIEST_MODE_ENABLED', 'true', 'db'), ...PRIEST_CONFIG.slice(1)])

    // Act
    fireEvent.click(killSwitch())
    const dialog = await screen.findByRole('alertdialog')
    fireEvent.click(within(dialog).getByRole('button', { name: /turn on/i }))

    // Assert
    await waitFor(() => expect(killSwitch().getAttribute('aria-checked')).toBe('true'))
  })

  it('locks the switch while the change is being saved', async () => {
    // Arrange
    setup({ [CONFIG_PUT]: () => new Promise(() => undefined) })
    await screen.findByText('qwen-test')

    // Act
    fireEvent.click(killSwitch())
    const dialog = await screen.findByRole('alertdialog')
    fireEvent.click(within(dialog).getByRole('button', { name: /turn on/i }))

    // Assert
    await waitFor(() => expect((killSwitch() as HTMLButtonElement).disabled).toBe(true))
  })

  it('shows an error with a retry when the status cannot be loaded', async () => {
    // Arrange / Act
    setup({ [CONFIG_GET]: new ApiError('Forbidden', 403) })

    // Assert
    expect(await screen.findByText('Forbidden')).toBeTruthy()
    expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy()
  })
})
