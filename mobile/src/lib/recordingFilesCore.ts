// Auri — deleting the phone's own copies of a recording
//
// The recorder writes the confessor's unmasked voice to the app cache, and the
// masking step writes a second copy there. Nothing else ever removes either, so
// both outlive the confession. This module deletes them, and does it only to
// files it can tell are ours: the review screen reads the file to delete from a
// route parameter, which a link can set, so a path is never trusted on its own.

/** The few things needed from the file system, so the rules here can be tested. */
export interface RecordingFilePort {
  /** The app cache directory with a trailing slash, or null if unavailable. */
  cacheDirectory: string | null;
  /** Names inside a directory; rejects if it does not exist. */
  readDirectory: (directoryUri: string) => Promise<string[]>;
  /** Delete one file; must not reject if it is already gone. */
  remove: (uri: string) => Promise<void>;
}

/**
 * Where expo-av writes recordings, as `recording-<uuid>.<ext>`: `<cache>/Audio/` on
 * Android (AVManager.java) and `<cache>/AV/` on iOS (EXAV.m). The UUID is upper-case
 * on iOS.
 */
const RECORDER_SUBDIRECTORIES = ['Audio/', 'AV/'];
const RECORDER_FILE = /^recording-[0-9a-f-]+\.[a-z0-9]+$/i;
/** `useAudioRecorder` writes the masked copy to `<cache>/masked_<timestamp>.wav`. */
const MASKED_FILE = /^masked_\d+\.wav$/;

/**
 * Whether *uri* is, by location and name, a recording this app made.
 *
 * Anything outside the cache, anything that climbs out of it, and any file that
 * does not look like a recorder or masking output is not ours to delete.
 */
function isOurRecording(uri: string, cacheDirectory: string | null): boolean {
  if (!cacheDirectory || !uri.startsWith(cacheDirectory)) return false;
  // The name patterns below allow no path separator, so `..` cannot get past them.
  const relative = uri.slice(cacheDirectory.length);
  const directory = RECORDER_SUBDIRECTORIES.find((sub) => relative.startsWith(sub));
  if (directory) return RECORDER_FILE.test(relative.slice(directory.length));
  return MASKED_FILE.test(relative);
}

/**
 * Delete one recording this app made.
 *
 * @returns true if it was deleted; false if the file is not one of ours or the
 *   delete failed. Never throws: cleaning up must not break the screen using it.
 */
export async function deleteRecordingFile(
  port: RecordingFilePort,
  uri: string,
): Promise<boolean> {
  if (!isOurRecording(uri, port.cacheDirectory)) return false;
  try {
    await port.remove(uri);
    return true;
  } catch {
    return false;
  }
}

/** Names in *directoryUri* that *matches*, or none if it cannot be read. */
async function matchingNames(
  port: RecordingFilePort,
  directoryUri: string,
  matches: RegExp,
): Promise<string[]> {
  try {
    return (await port.readDirectory(directoryUri)).filter((name) => matches.test(name));
  } catch {
    return [];
  }
}

/**
 * Delete every recording left in the cache, for the app's start-up.
 *
 * Covers what the screens' own clean-up misses: a force-closed app, a crash, a
 * back gesture out of the review screen. At a cold start nothing is in flight,
 * so every recording found is leftover.
 *
 * @returns How many files were deleted. Never throws.
 */
export async function purgeRecordingCache(port: RecordingFilePort): Promise<number> {
  const cache = port.cacheDirectory;
  if (!cache) return 0;

  const recorderUris = await Promise.all(
    RECORDER_SUBDIRECTORIES.map(async (sub) =>
      (await matchingNames(port, `${cache}${sub}`, RECORDER_FILE)).map(
        (name) => `${cache}${sub}${name}`,
      ),
    ),
  );
  const uris = [
    ...(await matchingNames(port, cache, MASKED_FILE)).map((name) => `${cache}${name}`),
    ...recorderUris.flat(),
  ];

  let removed = 0;
  for (const uri of uris) {
    if (await deleteRecordingFile(port, uri)) removed += 1;
  }
  return removed;
}
