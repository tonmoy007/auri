// Auri — the phone's own copies of a recording, deleted when no longer needed
//
// The rules (which files count as ours, and never throwing) live in
// recordingFilesCore so they can be tested without React Native.

import * as FileSystem from 'expo-file-system';
import {
  deleteRecordingFile as deleteWith,
  purgeRecordingCache as purgeWith,
  type RecordingFilePort,
} from './recordingFilesCore';

const port: RecordingFilePort = {
  cacheDirectory: FileSystem.cacheDirectory,
  readDirectory: (directoryUri) => FileSystem.readDirectoryAsync(directoryUri),
  remove: (uri) => FileSystem.deleteAsync(uri, { idempotent: true }),
};

/** Delete a recording this app made; ignores anything else, including a missing value. */
export function deleteRecordingFile(uri: string | null | undefined): Promise<boolean> {
  return uri ? deleteWith(port, uri) : Promise.resolve(false);
}

/** Delete every recording left in the cache. Call once, at app start. */
export function purgeRecordingCache(): Promise<number> {
  return purgeWith(port);
}
