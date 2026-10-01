interface ThemeNegativeShareCellProps {
  /** Fraction 0-1, or `null` when withheld. */
  share: number | null
}

/** A theme's negative share as a whole percentage, or "Hidden". */
export function ThemeNegativeShareCell({ share }: ThemeNegativeShareCellProps) {
  if (share === null) return <span className="text-sm text-muted-foreground">Hidden</span>
  return <span>{Math.round(share * 100)}%</span>
}
