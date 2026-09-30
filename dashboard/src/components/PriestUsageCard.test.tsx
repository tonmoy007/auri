import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PriestUsageCard } from '@/components/PriestUsageCard'
import { ApiError } from '@/lib/api'
import { makeRequester, USAGE } from '@/test/priestFixtures'

const auth = vi.hoisted(() => ({ request: null as unknown }))
vi.mock('@/hooks/useAuth', () => ({ useAuth: () => ({ authedRequest: auth.request }) }))

const USAGE_GET = 'GET /admin/priest/usage'

function setup(usage: unknown = USAGE) {
  auth.request = makeRequester({ [USAGE_GET]: usage })
  return render(<PriestUsageCard />)
}

function rowFor(label: string): HTMLElement {
  return screen.getByText(label).closest('tr') as HTMLElement
}

describe('PriestUsageCard', () => {
  beforeEach(() => {
    auth.request = null
  })

  it('shows a loading skeleton first', () => {
    // Arrange / Act
    setup()

    // Assert
    expect(screen.getByRole('status', { name: /loading usage/i })).toBeTruthy()
  })

  it('shows outcome counts and replaces a suppressed count with "fewer than N"', async () => {
    // Arrange / Act
    setup()
    await screen.findByText('Answer')

    // Assert
    expect(within(rowFor('Answer')).getByText('31')).toBeTruthy()
    expect(within(rowFor('Crisis')).getByText('fewer than 5')).toBeTruthy()
    expect(within(rowFor('Crisis')).queryByText(/^\d+$/)).toBeNull()
  })

  it('shows latency p50 and p95 per stage, in milliseconds or seconds', async () => {
    // Arrange / Act
    setup()
    await screen.findByText('Total')

    // Assert
    expect(within(rowFor('Total')).getByText('1.50 s')).toBeTruthy()
    expect(within(rowFor('Total')).getByText('400 ms')).toBeTruthy()
  })

  it('says the figures cover the time since the process started', async () => {
    // Arrange / Act
    setup()

    // Assert
    expect(await screen.findByText(/since the server process started/i)).toBeTruthy()
  })

  it('shows an empty state before any question was asked', async () => {
    // Arrange / Act
    setup({ ...USAGE, outcomes: [], latency: [] })

    // Assert
    expect(await screen.findByText(/no questions since the server process started/i)).toBeTruthy()
  })

  it('shows an error with a retry that loads again', async () => {
    // Arrange
    const request = makeRequester({ [USAGE_GET]: new ApiError('Service Unavailable', 503) })
    auth.request = request
    render(<PriestUsageCard />)
    await screen.findByText('Service Unavailable')
    request.routes[USAGE_GET] = USAGE

    // Act
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))

    // Assert
    expect(await screen.findByText('Answer')).toBeTruthy()
  })
})
