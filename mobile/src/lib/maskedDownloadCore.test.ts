import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The endpoint table lives beside native settings code; only the paths are needed.
vi.mock('expo-secure-store', () => ({}));

import {
  downloadMaskedAudio,
  maskedDownloadUrl,
  maskUploadUrl,
  type DownloadPort,
} from './maskedDownloadCore';

const HEADERS = { 'X-Device-Token-Hash': 'abc' }

function port(outcome: Promise<{ status: number }>) {
  const cancel = vi.fn(async () => undefined)
  const start = vi.fn(() => ({ result: outcome, cancel }))
  return { port: { start } satisfies DownloadPort, start, cancel }
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('masked download URLs', () => {
  it('asks the mask endpoint for a download id instead of base64', () => {
    // Arrange
    const base = 'https://api.example'

    // Act
    const url = maskUploadUrl(base)

    // Assert
    expect(url).toBe('https://api.example/api/v1/voice/mask?delivery=download')
  })

  it('fetches the masked file by its id, encoded', () => {
    // Arrange
    const id = 'a/b c'

    // Act
    const url = maskedDownloadUrl('https://api.example', id)

    // Assert
    expect(url).toBe('https://api.example/api/v1/voice/masked/a%2Fb%20c')
  })
})

describe('downloadMaskedAudio', () => {
  it('writes the file and reports success on a 200', async () => {
    // Arrange
    const fake = port(Promise.resolve({ status: 200 }))

    // Act
    const ok = await downloadMaskedAudio(fake.port, 'u', 'file:///m.wav', HEADERS, 1000)

    // Assert
    expect(ok).toBe(true)
    expect(fake.start).toHaveBeenCalledWith('u', 'file:///m.wav', HEADERS)
  })

  it('reports failure for any other status, such as an expired id', async () => {
    // Arrange
    const fake = port(Promise.resolve({ status: 404 }))

    // Act
    const ok = await downloadMaskedAudio(fake.port, 'u', 'f', HEADERS, 1000)

    // Assert
    expect(ok).toBe(false)
  })

  it('cancels a download that runs past its budget', async () => {
    // Arrange — the download never finishes on its own
    const fake = port(new Promise(() => undefined))

    // Act
    const pending = downloadMaskedAudio(fake.port, 'u', 'f', HEADERS, 1000)
    await vi.advanceTimersByTimeAsync(1001)
    const ok = await pending

    // Assert
    expect(ok).toBe(false)
    expect(fake.cancel).toHaveBeenCalledTimes(1)
  })

  it('reports failure when the download errors', async () => {
    // Arrange
    const fake = port(Promise.reject(new Error('offline')))

    // Act
    const ok = await downloadMaskedAudio(fake.port, 'u', 'f', HEADERS, 1000)

    // Assert
    expect(ok).toBe(false)
  })
})
