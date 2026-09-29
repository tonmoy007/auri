import { ThemeNegativeShareCell } from '@/components/ThemeNegativeShareCell'
import { ThemePreviousPeriodCell } from '@/components/ThemePreviousPeriodCell'
import { ThemeSentimentCell } from '@/components/ThemeSentimentCell'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import type { ThemeEntry } from '@/lib/api'

interface ThemesTableProps {
  themes: ThemeEntry[]
  minCohort: number
}

/** Ranked recurring themes; every withheld figure says so in words. */
export function ThemesTable({ themes, minCohort }: ThemesTableProps) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-12">#</TableHead>
          <TableHead>Theme</TableHead>
          <TableHead>Confessions</TableHead>
          <TableHead>Previous period</TableHead>
          <TableHead>Negative</TableHead>
          <TableHead>Change</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {themes.map((theme) => (
          <TableRow key={theme.rank}>
            <TableCell>{theme.rank}</TableCell>
            <TableCell className="font-medium">{theme.label}</TableCell>
            <TableCell>{theme.confessions}</TableCell>
            <TableCell>
              <ThemePreviousPeriodCell bucket={theme.previous_period} minCohort={minCohort} />
            </TableCell>
            <TableCell>
              <ThemeNegativeShareCell share={theme.negative_share} />
            </TableCell>
            <TableCell>
              <ThemeSentimentCell theme={theme} minCohort={minCohort} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
