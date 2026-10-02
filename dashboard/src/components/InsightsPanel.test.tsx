import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { InsightsPanel } from '@/components/InsightsPanel'
import { InsightsWeek } from '@/components/InsightsWeek'
import type { Bucket, Insights, WeekInsights } from '@/lib/api'
import { makeRequester, type FakeRequester } from '@/test/priestFixtures'

const auth = vi.hoisted(() => ({ request: null as unknown }))
vi.mock('@/hooks/useAuth', () => ({ useAuth: () => ({ authedRequest: auth.request }) }))

const shown = (label: string, count: number): Bucket => ({ label, count, suppressed: false })
const hidden = (label: string): Bucket => ({ label, count: null, suppressed: true })

function week(label: string, start: string, end: string, frozen: boolean): WeekInsights {
  if (!frozen) {
    return {
      label, start, end, frozen, total: null, by_day: [], by_category: [],
      by_sentiment: [], by_department: [], by_status: [], delivery: [], delivery_time: [],
    }
  }
  return {
    label, start, end, frozen,
    total: shown('total', 12),
    by_day: [shown(start, 12)],
    by_category: [shown('workload', 12)],
    by_sentiment: [shown('negative', 12)],
    by_department: [shown('HR', 7), hidden('Finance')],
    by_status: [shown('pending', 5), shown('forwarded', 7), shown('flagged', 0)],
    delivery: [shown('delivered', 7), shown('not_delivered', 5)],
    delivery_time: [shown('1-6h', 7)],
  }
}

const SEPTEMBER: Insights = {
  month: '2026-09',
  min_cohort: 5,
  weeks: [
    week('W1', '2026-09-01', '2026-09-07', true),
    week('W2', '2026-09-08', '2026-09-14', true),
    week('W3', '2026-09-15', '2026-09-21', true),
    week('W4', '2026-09-22', '2026-09-30', false),
  ],
}

let request: FakeRequester

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(Date.UTC(2026, 9, 3, 9)))
  request = makeRequester({ 'GET /hr/insights?month=2026-09': SEPTEMBER })
  auth.request = request
})

afterEach(() => {
  vi.useRealTimers()
})

describe('InsightsPanel', () => {
  it('opens on the previous month early in a month and asks for that month only', async () => {
    // Arrange
    render(<InsightsPanel />)

    // Act
    await screen.findByText('1–7 Sep')

    // Assert
    expect(request.callsTo('GET /hr/insights?month=2026-09')).toHaveLength(1)
    expect(screen.getByLabelText('Month')).toHaveProperty('value', '2026-09')
  })

  it('marks a week that has not frozen yet', async () => {
    // Arrange
    render(<InsightsPanel />)

    // Act
    const lastWeek = await screen.findByRole('tab', { name: /22–30 Sep/ })

    // Assert
    expect(lastWeek.textContent).toContain('not ready')
  })

  it('shows the first frozen week straight away', async () => {
    // Arrange
    render(<InsightsPanel />)

    // Act
    await waitFor(() => expect(screen.getByText('Kept private')).toBeTruthy())

    // Assert
    expect(screen.getAllByText('12').length).toBeGreaterThan(0)
  })
})

describe('InsightsWeek', () => {
  it('explains why a week that is not frozen has no figures', () => {
    // Arrange
    const notReady = week('W4', '2026-09-22', '2026-09-30', false)

    // Act
    render(<InsightsWeek week={notReady} minCohort={5} />)

    // Assert
    expect(screen.getByText('Not available yet')).toBeTruthy()
    expect(screen.queryByText('Total')).toBeNull()
  })

  it('says a withheld figure is hidden, never shows it as zero', () => {
    // Arrange
    const frozen = week('W1', '2026-09-01', '2026-09-07', true)

    // Act
    render(<InsightsWeek week={frozen} minCohort={5} />)

    // Assert
    expect(screen.getByText('Finance')).toBeTruthy()
    expect(screen.getByText(/Hidden \(fewer than 5/)).toBeTruthy()
  })
})
