/**
 * Month and week helpers for the Insights tab (plan 14.1).
 *
 * Insights reports fixed, month-aligned weeks (1–7, 8–14, 15–21, 22–end), and a
 * week has figures only once it has ended and every confession in it has been
 * removed under the retention rule. These helpers keep that logic out of the
 * React components so it can be tested on its own.
 */

/** Days into a month before its first week can have frozen (7 days + a margin). */
const FIRST_WEEK_SETTLED_DAY = 9

const MONTH_NAMES = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
] as const

function monthKey(year: number, monthIndex: number): string {
  return `${year}-${String(monthIndex + 1).padStart(2, '0')}`
}

/** The current UTC month as `YYYY-MM`; the latest month the picker offers. */
export function latestMonth(today: Date): string {
  return monthKey(today.getUTCFullYear(), today.getUTCMonth())
}

/**
 * The month the tab opens on: the previous month early on, when no week of the
 * current month can have frozen yet, and the current month after that.
 */
export function defaultInsightsMonth(today: Date): string {
  if (today.getUTCDate() >= FIRST_WEEK_SETTLED_DAY) return latestMonth(today)
  const previous = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth() - 1, 1))
  return monthKey(previous.getUTCFullYear(), previous.getUTCMonth())
}

/** A fixed week's days as a short label, e.g. "22–30 Sep". */
export function weekRangeLabel(week: { start: string; end: string }): string {
  const [, month, firstDay] = week.start.split('-').map(Number)
  const lastDay = Number(week.end.split('-')[2])
  return `${firstDay}–${lastDay} ${MONTH_NAMES[month - 1]}`
}
