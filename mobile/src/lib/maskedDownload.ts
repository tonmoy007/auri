// Auri — the native side of downloading a masked recording (plan task 16.4)
//
// Binds maskedDownloadCore's port to expo-file-system, which streams the response
// straight to a file instead of through JS memory, sidestepping the unreliable
// fetch().blob() on this React Native stack.

import * as FileSystem from 'expo-file-system';
import type { DownloadPort } from './maskedDownloadCore';

export const expoDownloadPort: DownloadPort = {
  start: (url, fileUri, headers) => {
    const task = FileSystem.createDownloadResumable(url, fileUri, { headers });
    return {
      result: task.downloadAsync().then((outcome) => ({ status: outcome?.status ?? 0 })),
      cancel: async () => {
        try {
          await task.cancelAsync();
        } catch (_error: unknown) {
          // Already finished or never started: nothing left to stop.
        }
      },
    };
  },
};
