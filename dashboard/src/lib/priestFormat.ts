/** Display helpers for the Guide tab. Pure functions, no network. */

const NO_VALUE = '—'
/** A report value longer than this is not shown: reports carry numbers, not text. */
const MAX_REPORT_TEXT = 60
const VERDICT_FLAGS = ['gate_passed', 'gates_passed', 'passed', 'pass'] as const
const VERDICT_TEXT_KEYS = ['verdict', 'gate', 'status'] as const

export interface ReportRow {
  label: string
  value: string
}

/** `library_excerpts` becomes `Library excerpts`. */
export function humanizeKey(key: string): string {
  const spaced = key.replace(/_/g, ' ').trim()
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

/** A count, or "fewer than N" for a suppressed one, so a small number is never shown. */
export function formatCount(count: number | null, suppressed: boolean, minCohort: number): string {
  if (suppressed || count === null) return `fewer than ${minCohort}`
  return String(count)
}

/** Seconds as milliseconds below one second, otherwise as seconds. */
export function formatSeconds(value: number | null): string {
  if (value === null) return NO_VALUE
  if (value < 1) return `${Math.round(value * 1000)} ms`
  return `${value.toFixed(2)} s`
}

/** An ISO timestamp in the viewer's locale; an unreadable one is shown as it came. */
export function formatWhen(value: string | null): string {
  if (value === null) return NO_VALUE
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function verdictFromText(text: string): 'pass' | 'fail' | null {
  const lowered = text.toLowerCase()
  if (lowered === 'pass' || lowered === 'passed') return 'pass'
  if (lowered === 'fail' || lowered === 'failed') return 'fail'
  return null
}

/** Pass or fail when the report states a gate outcome, otherwise null. */
export function reportVerdict(summary: Record<string, unknown>): 'pass' | 'fail' | null {
  for (const key of VERDICT_FLAGS) {
    const flag = summary[key]
    if (typeof flag === 'boolean') return flag ? 'pass' : 'fail'
  }
  for (const key of VERDICT_TEXT_KEYS) {
    const text = summary[key]
    if (typeof text === 'string') return verdictFromText(text)
  }
  return null
}

function formatScalar(value: unknown): string | null {
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(3)
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'string' && value.length <= MAX_REPORT_TEXT) return value
  return null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function flatten(summary: Record<string, unknown>, prefix: string, depth: number): ReportRow[] {
  const rows: ReportRow[] = []
  for (const [key, value] of Object.entries(summary)) {
    const label = prefix === '' ? humanizeKey(key) : `${prefix} ${key.replace(/_/g, ' ')}`
    if (isRecord(value) && depth > 0) {
      rows.push(...flatten(value, label, depth - 1))
      continue
    }
    const text = formatScalar(value)
    if (text !== null) rows.push({ label, value: text })
  }
  return rows
}

/** Report figures as label and value rows; lists, nulls and long text are dropped. */
export function reportRows(summary: Record<string, unknown>): ReportRow[] {
  return flatten(summary, '', 1)
}
