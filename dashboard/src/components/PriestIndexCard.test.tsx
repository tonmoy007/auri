import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PriestIndexCard } from '@/components/PriestIndexCard'
import {
  IDLE_STATUS,
  INDEX,
  MANIFEST,
  makeRequester,
  status,
  type FakeRequester,
} from '@/test/priestFixtures'

const auth = vi.hoisted(() => ({ request: null as unknown }))
vi.mock('@/hooks/useAuth', () => ({ useAuth: () => ({ authedRequest: auth.request }) }))

const INDEX_GET = 'GET /admin/priest/index'
const STATUS_GET = 'GET /admin/priest/reindex/status'
const REINDEX_POST = 'POST /admin/priest/reindex'
const ACTIVATE_POST = 'POST /admin/priest/activate'

let request: FakeRequester

function setup(routes: Record<string, unknown> = {}) {
  request = makeRequester({
    [INDEX_GET]: INDEX,
    [STATUS_GET]: IDLE_STATUS,
    [REINDEX_POST]: { state: 'running', started_at: '2026-09-30T10:00:00Z' },
    [ACTIVATE_POST]: { active_version: 'v1' },
    ...routes,
  })
  auth.request = request
  return render(<PriestIndexCard />)
}

function fact(term: string): string {
  const dt = screen.getByText(term, { selector: 'dt' })
  return dt.nextElementSibling?.textContent ?? ''
}

describe('PriestIndexCard', () => {
  beforeEach(() => {
    auth.request = null
  })

  it('shows a loading skeleton first', () => {
    // Arrange / Act
    setup()

    // Assert
    expect(screen.getByRole('status', { name: /loading the index/i })).toBeTruthy()
  })

  it('shows what the active index holds', async () => {
    // Arrange / Act
    setup()
    await screen.findByText('Notes')

    // Assert
    expect(fact('Active version')).toBe('v2')
    expect(fact('Notes')).toBe('210')
    expect(fact('Chunks')).toBe('1234')
    expect(fact('Embedding model')).toBe('embed-a (768 dimensions)')
    expect(fact('Build time')).toBe('42 s')
    expect(fact('Unresolved links')).toBe('7')
    expect(fact('Build warnings')).toBe('3')
  })

  it('lists exclusions by reason and never prints the warning text', async () => {
    // Arrange / Act
    setup()
    await screen.findByText('Notes')

    // Assert
    expect(screen.getByText('Private')).toBeTruthy()
    expect(screen.getByText('Too short')).toBeTruthy()
    expect(screen.queryByText('w1')).toBeNull()
  })

  it('tells the operator when the next build will use a different embedding model', async () => {
    // Arrange / Act
    setup({ [INDEX_GET]: { ...INDEX, configured_embed_model: 'embed-b' } })

    // Assert
    expect(await screen.findByText(/next build will use embed-b/i)).toBeTruthy()
  })

  it('shows an empty state when no index was ever built', async () => {
    // Arrange / Act
    setup({
      [INDEX_GET]: { active_version: null, manifest: null, versions: [], configured_embed_model: 'embed-a' },
    })

    // Assert
    expect(await screen.findByText(/no index has been built yet/i)).toBeTruthy()
  })

  it('shows the error code of a failed build', async () => {
    // Arrange / Act
    setup({ [STATUS_GET]: status({ state: 'failed', error_code: 'builder_died' }) })

    // Assert
    expect(await screen.findByText('builder_died')).toBeTruthy()
    expect(screen.getByText('failed')).toBeTruthy()
  })

  it('shows progress while a build runs and blocks a second one', async () => {
    // Arrange / Act
    setup({ [STATUS_GET]: status({ state: 'running', notes_total: 10, notes_indexed: 3 }) })
    const bar = await screen.findByRole('progressbar')

    // Assert
    expect(bar.getAttribute('aria-valuenow')).toBe('30')
    expect(screen.getByText(/3 of 10 notes/i)).toBeTruthy()
    expect((screen.getByRole('button', { name: /reindexing/i }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('asks before reindexing and only then starts the build', async () => {
    // Arrange
    setup()
    await screen.findByText('Notes')

    // Act
    fireEvent.click(screen.getByRole('button', { name: /^reindex$/i }))
    const dialog = await screen.findByRole('alertdialog')
    const startedBeforeConfirm = request.callsTo(REINDEX_POST).length
    fireEvent.click(within(dialog).getByRole('button', { name: /start reindex/i }))

    // Assert
    expect(startedBeforeConfirm).toBe(0)
    await waitFor(() => expect(request.callsTo(REINDEX_POST)).toHaveLength(1))
  })

  it('does not reindex when the confirmation is cancelled', async () => {
    // Arrange
    setup()
    await screen.findByText('Notes')

    // Act
    fireEvent.click(screen.getByRole('button', { name: /^reindex$/i }))
    const dialog = await screen.findByRole('alertdialog')
    fireEvent.click(within(dialog).getByRole('button', { name: /cancel/i }))

    // Assert
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(request.callsTo(REINDEX_POST)).toHaveLength(0)
  })

  it('offers Activate on older versions only and asks before rolling back', async () => {
    // Arrange
    setup()
    await screen.findByText('Notes')

    // Act
    const activateButtons = screen.getAllByRole('button', { name: /^activate/i })
    fireEvent.click(activateButtons[0])
    const dialog = await screen.findByRole('alertdialog')
    const activatedBeforeConfirm = request.callsTo(ACTIVATE_POST).length
    fireEvent.click(within(dialog).getByRole('button', { name: /^activate v1$/i }))

    // Assert
    expect(activateButtons).toHaveLength(1)
    expect(screen.getByText('Active', { selector: '[data-slot="badge"]' })).toBeTruthy()
    expect(activatedBeforeConfirm).toBe(0)
    await waitFor(() => expect(request.callsTo(ACTIVATE_POST)).toHaveLength(1))
    expect(request.callsTo(ACTIVATE_POST)[0].body).toEqual({ version: 'v1' })
  })

  it('does not activate when the rollback is cancelled', async () => {
    // Arrange
    setup()
    await screen.findByText('Notes')

    // Act
    fireEvent.click(screen.getByRole('button', { name: /^activate/i }))
    const dialog = await screen.findByRole('alertdialog')
    fireEvent.click(within(dialog).getByRole('button', { name: /cancel/i }))

    // Assert
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(request.callsTo(ACTIVATE_POST)).toHaveLength(0)
  })

  it('shows the manifest figures for the manifest it was given', async () => {
    // Arrange / Act
    setup({ [INDEX_GET]: { ...INDEX, manifest: { ...MANIFEST, note_count: 5, chunk_count: 6 } } })
    await screen.findByText('Notes')

    // Assert
    expect(fact('Notes')).toBe('5')
    expect(fact('Chunks')).toBe('6')
  })
})
