import { describe, expect, it } from 'vitest'
import { defaultInsightsMonth, latestMonth, weekRangeLabel } from '@/lib/insightsMonth'

describe('defaultInsightsMonth', () => {
  it('opens on the previous month during the first week, when nothing has frozen yet', () => {
    // Arrange
    const today = new Date(Date.UTC(2026, 9, 3))

    // Act
    const month = defaultInsightsMonth(today)

    // Assert
    expect(month).toBe('2026-09')
  })

  it('opens on the current month once its first week can have frozen', () => {
    // Arrange
    const today = new Date(Date.UTC(2026, 9, 12))

    // Act
    const month = defaultInsightsMonth(today)

    // Assert
    expect(month).toBe('2026-10')
  })

  it('rolls back across a year boundary', () => {
    // Arrange
    const today = new Date(Date.UTC(2027, 0, 2))

    // Act
    const month = defaultInsightsMonth(today)

    // Assert
    expect(month).toBe('2026-12')
  })
})

describe('latestMonth', () => {
  it('is the current UTC month, the latest a picker may offer', () => {
    // Arrange
    const today = new Date(Date.UTC(2026, 9, 31, 23, 30))

    // Act
    const month = latestMonth(today)

    // Assert
    expect(month).toBe('2026-10')
  })
})

describe('weekRangeLabel', () => {
  it('names a fixed week by its days', () => {
    // Arrange
    const week = { label: 'W4', start: '2026-09-22', end: '2026-09-30' }

    // Act
    const label = weekRangeLabel(week)

    // Assert
    expect(label).toBe('22–30 Sep')
  })
})
