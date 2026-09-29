import { Button } from '@/components/ui/button'
import { downloadTextFile } from '@/lib/download'
import type { ThemesReport } from '@/lib/api'

interface ThemesDigestActionsProps {
  report: ThemesReport
}

// Excel only reads a UTF-8 CSV as UTF-8 when it starts with a byte order mark.
const CSV_BYTE_ORDER_MARK = '\uFEFF'

function digestFilename(report: ThemesReport, extension: string): string {
  return `auri-digest-${report.range_end.slice(0, 10)}.${extension}`
}

/** Download the digest exactly as generated with the themes on screen. */
export function ThemesDigestActions({ report }: ThemesDigestActionsProps) {
  return (
    <div className="flex flex-wrap gap-2">
      <Button
        variant="outline"
        onClick={() =>
          downloadTextFile(
            digestFilename(report, 'md'),
            'text/markdown;charset=utf-8',
            report.digest_markdown,
          )
        }
      >
        Download Markdown
      </Button>
      <Button
        variant="outline"
        onClick={() =>
          downloadTextFile(
            digestFilename(report, 'csv'),
            'text/csv;charset=utf-8',
            CSV_BYTE_ORDER_MARK + report.digest_csv,
          )
        }
      >
        Download CSV
      </Button>
    </div>
  )
}
