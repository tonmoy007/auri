import { describe, expect, it } from 'vitest'
import {
  formatCount,
  formatSeconds,
  humanizeKey,
  reportRows,
  reportVerdict,
} from '@/lib/priestFormat'

describe('humanizeKey', () => {
  it('turns a snake_case outcome into a readable label', () => {
    // Arrange
    const key = 'library_excerpts'

    // Act
    const label = humanizeKey(key)

    // Assert
    expect(label).toBe('Library excerpts')
  })
})

describe('formatCount', () => {
  it('shows a plain number when the count is reported', () => {
    // Arrange / Act
    const text = formatCount(12, false, 5)

    // Assert
    expect(text).toBe('12')
  })

  it('shows "fewer than N" for a suppressed count', () => {
    // Arrange / Act
    const text = formatCount(null, true, 5)

    // Assert
    expect(text).toBe('fewer than 5')
  })

  it('never shows a number for a suppressed bucket even if one slipped through', () => {
    // Arrange / Act
    const text = formatCount(2, true, 5)

    // Assert
    expect(text).toBe('fewer than 5')
  })
})

describe('formatSeconds', () => {
  it('shows milliseconds under a second', () => {
    // Arrange / Act / Assert
    expect(formatSeconds(0.25)).toBe('250 ms')
  })

  it('shows seconds with two decimals from one second up', () => {
    // Arrange / Act / Assert
    expect(formatSeconds(3.456)).toBe('3.46 s')
  })

  it('shows a dash when there is no sample', () => {
    // Arrange / Act / Assert
    expect(formatSeconds(null)).toBe('—')
  })
})

describe('reportVerdict', () => {
  it('reads a boolean gate flag', () => {
    // Arrange / Act / Assert
    expect(reportVerdict({ gate_passed: true })).toBe('pass')
    expect(reportVerdict({ passed: false })).toBe('fail')
  })

  it('reads a textual verdict', () => {
    // Arrange / Act / Assert
    expect(reportVerdict({ verdict: 'PASS' })).toBe('pass')
    expect(reportVerdict({ gate: 'failed' })).toBe('fail')
  })

  it('is unknown when the summary carries no gate', () => {
    // Arrange / Act / Assert
    expect(reportVerdict({ recall_at_6: 0.9 })).toBeNull()
  })
})

describe('reportRows', () => {
  it('flattens numbers, booleans and short strings and drops lists and nulls', () => {
    // Arrange
    const summary = {
      recall_at_6: 0.91234,
      cases: 40,
      latency: { p50: 1.2, p95: 3.4 },
      gate_passed: true,
      skipped: null,
      items: ['a', 'b'],
    }

    // Act
    const rows = reportRows(summary)

    // Assert
    expect(rows).toEqual([
      { label: 'Recall at 6', value: '0.912' },
      { label: 'Cases', value: '40' },
      { label: 'Latency p50', value: '1.200' },
      { label: 'Latency p95', value: '3.400' },
      { label: 'Gate passed', value: 'Yes' },
    ])
  })

  it('refuses to show a long string, in case a report ever carried text', () => {
    // Arrange
    const summary = { note: 'x'.repeat(200), cases: 3 }

    // Act
    const rows = reportRows(summary)

    // Assert
    expect(rows).toEqual([{ label: 'Cases', value: '3' }])
  })
})
