import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PriestEvalCard } from '@/components/PriestEvalCard'
import { makeRequester } from '@/test/priestFixtures'

const auth = vi.hoisted(() => ({ request: null as unknown }))
vi.mock('@/hooks/useAuth', () => ({ useAuth: () => ({ authedRequest: auth.request }) }))

function setup(report: unknown) {
  auth.request = makeRequester({ 'GET /admin/priest/report': report })
  return render(<PriestEvalCard />)
}

describe('PriestEvalCard', () => {
  beforeEach(() => {
    auth.request = null
  })

  it('shows a friendly empty state when no report has been written', async () => {
    // Arrange / Act
    setup({ available: false, summary: null })

    // Assert
    expect(await screen.findByText(/no evaluation report yet/i)).toBeTruthy()
  })

  it('shows the figures and a pass badge when the gate passed', async () => {
    // Arrange / Act
    setup({
      available: true,
      summary: { recall_at_6: 0.9123, gate_passed: true, latency: { p50: 1.2, p95: 3.4 } },
    })

    // Assert
    expect(await screen.findByText('Gate passed')).toBeTruthy()
    expect(screen.getByText('Pass', { selector: '[data-slot="badge"]' }).className).not.toContain(
      'text-destructive',
    )
    expect(screen.getByText('0.912')).toBeTruthy()
    expect(screen.getByText('Latency p95')).toBeTruthy()
  })

  it('shows a fail badge when the gate failed', async () => {
    // Arrange / Act
    setup({ available: true, summary: { passed: false, cases: 40 } })

    // Assert
    const badge = await screen.findByText('Fail', { selector: '[data-slot="badge"]' })
    expect(badge.className).toContain('text-destructive')
  })

  it('shows no badge when the report states no gate', async () => {
    // Arrange / Act
    setup({ available: true, summary: { cases: 40 } })
    await screen.findByText('Cases')

    // Assert
    expect(screen.queryByText('Pass', { selector: '[data-slot="badge"]' })).toBeNull()
    expect(screen.queryByText('Fail', { selector: '[data-slot="badge"]' })).toBeNull()
  })
})
