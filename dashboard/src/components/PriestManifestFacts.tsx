import { Alert, AlertDescription } from '@/components/ui/alert'
import { humanizeKey, formatWhen } from '@/lib/priestFormat'
import type { PriestIndexInfo, PriestManifest } from '@/lib/api'

function rowsFor(manifest: PriestManifest): [string, string][] {
  return [
    ['Active version', manifest.version],
    ['Built', formatWhen(manifest.created_at)],
    ['Build time', `${manifest.build_seconds} s`],
    ['Notes', String(manifest.note_count)],
    ['Chunks', String(manifest.chunk_count)],
    ['Embedding model', `${manifest.embed_model} (${manifest.dim} dimensions)`],
    ['Unresolved links', String(manifest.unresolved_links)],
    // Only the count: a warning can name a note, and this page shows no note text.
    ['Build warnings', String(manifest.warnings.length)],
  ]
}

/** What the active index holds, as counts. No note text or titles are shown. */
export function PriestManifestFacts({ index }: { index: PriestIndexInfo }) {
  const { manifest } = index
  if (manifest === null) {
    return (
      <p className="text-sm text-muted-foreground">
        No index has been built yet. Run a reindex to build the first one.
      </p>
    )
  }
  const exclusions = Object.entries(manifest.exclusions)
  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-sm">
        {rowsFor(manifest).map(([term, value]) => (
          <div key={term} className="contents">
            <dt className="text-muted-foreground">{term}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <div>
        <p className="text-sm text-muted-foreground">Notes left out, by reason</p>
        {exclusions.length === 0 ? (
          <p className="text-sm">None</p>
        ) : (
          <ul className="flex flex-wrap gap-x-6 text-sm">
            {exclusions.map(([reason, count]) => (
              <li key={reason}>
                <span>{humanizeKey(reason)}</span>: {count}
              </li>
            ))}
          </ul>
        )}
      </div>
      {index.configured_embed_model !== manifest.embed_model && (
        <Alert>
          <AlertDescription>
            The next build will use {index.configured_embed_model}; the active index was built with{' '}
            {manifest.embed_model}.
          </AlertDescription>
        </Alert>
      )}
    </div>
  )
}
