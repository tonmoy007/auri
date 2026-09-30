import { describe, it, expect, vi } from 'vitest';
import {
  deleteRecordingFile,
  purgeRecordingCache,
  type RecordingFilePort,
} from './recordingFilesCore';

const CACHE = 'file:///data/user/0/app.auri/cache/';

/** A port over a fake cache: a map of directory URI to the names inside it. */
function fakePort(
  directories: Record<string, string[]>,
  overrides: Partial<RecordingFilePort> = {},
): RecordingFilePort & { removed: string[] } {
  const removed: string[] = [];
  return {
    cacheDirectory: CACHE,
    readDirectory: async (uri) => {
      const names = directories[uri];
      if (!names) throw new Error(`no such directory: ${uri}`);
      return names;
    },
    remove: async (uri) => {
      removed.push(uri);
    },
    ...overrides,
    removed,
  };
}

describe('deleteRecordingFile', () => {
  it.each([
    ['an Android recorder file', `${CACHE}Audio/recording-3f2a9c1e-7b64-4d10-9a55-0c8e1d2b7f44.aac`],
    ['an iOS recorder file', `${CACHE}AV/recording-3F2A9C1E-7B64-4D10-9A55-0C8E1D2B7F44.aac`],
    ['a masked file', `${CACHE}masked_1790000000000.wav`],
  ])('deletes %s inside the cache', async (_label, uri) => {
    const port = fakePort({});

    const deleted = await deleteRecordingFile(port, uri);

    expect(deleted).toBe(true);
    expect(port.removed).toEqual([uri]);
  });

  it.each([
    ['a file outside the cache', 'file:///data/user/0/app.auri/files/masked_1.wav'],
    ['a path that climbs out of the cache', `${CACHE}Audio/../../files/recording-a.aac`],
    ['an unrelated cache file', `${CACHE}important.db`],
    ['a masked name with a suffix', `${CACHE}masked_1.wav.bak`],
    ['a recorder name outside Audio', `${CACHE}recording-abc.aac`],
    ['a remote address', 'https://example.test/masked_1.wav'],
    ['a recorder name that climbs out through a separator', `${CACHE}Audio/recording-a/../../files/secret.aac`],
    ['a recorder name with a nested path', `${CACHE}AV/recording-a/b.aac`],
    ['a masked name that climbs out through a separator', `${CACHE}masked_1/../../files/secret.wav`],
    ['an empty value', ''],
  ])('refuses %s', async (_label, uri) => {
    // The review screen reads this value from a route param, which a link can set
    const port = fakePort({});

    const deleted = await deleteRecordingFile(port, uri);

    expect(deleted).toBe(false);
    expect(port.removed).toEqual([]);
  });

  it('refuses everything when the cache directory is unknown', async () => {
    const port = fakePort({}, { cacheDirectory: null });

    expect(await deleteRecordingFile(port, `${CACHE}masked_1.wav`)).toBe(false);
    expect(port.removed).toEqual([]);
  });

  it('reports a failed delete instead of throwing', async () => {
    const port = fakePort(
      {},
      {
        remove: vi.fn().mockRejectedValue(new Error('disk error')),
      },
    );

    await expect(deleteRecordingFile(port, `${CACHE}masked_1.wav`)).resolves.toBe(false);
  });
});

describe('purgeRecordingCache', () => {
  it('removes leftover recordings and masked files, and nothing else', async () => {
    const port = fakePort({
      [CACHE]: ['masked_1.wav', 'masked_2.wav', 'image.png', 'Audio', 'masked_notes.txt'],
      [`${CACHE}Audio/`]: [
        'recording-3f2a9c1e-7b64-4d10-9a55-0c8e1d2b7f44.aac',
        'recording-aaaa.m4a',
        'README.txt',
      ],
      [`${CACHE}AV/`]: ['recording-3F2A9C1E-7B64-4D10-9A55-0C8E1D2B7F44.aac', 'notes.txt'],
    });

    const removed = await purgeRecordingCache(port);

    expect(removed).toBe(5);
    expect(port.removed.sort()).toEqual(
      [
        `${CACHE}masked_1.wav`,
        `${CACHE}masked_2.wav`,
        `${CACHE}Audio/recording-3f2a9c1e-7b64-4d10-9a55-0c8e1d2b7f44.aac`,
        `${CACHE}Audio/recording-aaaa.m4a`,
        `${CACHE}AV/recording-3F2A9C1E-7B64-4D10-9A55-0C8E1D2B7F44.aac`,
      ].sort(),
    );
  });

  it('copes with no recorder directory yet', async () => {
    const port = fakePort({ [CACHE]: ['masked_1.wav'] });

    await expect(purgeRecordingCache(port)).resolves.toBe(1);
  });

  it('keeps going when one file will not delete', async () => {
    const remove = vi
      .fn()
      .mockRejectedValueOnce(new Error('locked'))
      .mockResolvedValue(undefined);
    const port = fakePort({ [CACHE]: ['masked_1.wav', 'masked_2.wav'] }, { remove });

    const removed = await purgeRecordingCache(port);

    expect(remove).toHaveBeenCalledTimes(2);
    expect(removed).toBe(1);
  });

  it('never throws, even when the cache cannot be read', async () => {
    const port = fakePort({}, { readDirectory: vi.fn().mockRejectedValue(new Error('no')) });

    await expect(purgeRecordingCache(port)).resolves.toBe(0);
  });

  it('does nothing when the cache directory is unknown', async () => {
    const port = fakePort({}, { cacheDirectory: null });

    await expect(purgeRecordingCache(port)).resolves.toBe(0);
    expect(port.removed).toEqual([]);
  });
});
