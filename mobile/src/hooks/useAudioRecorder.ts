// Auri — useAudioRecorder hook
// Custom hook wrapping expo-av for audio recording with permission management

import { useState, useCallback, useRef, useEffect } from 'react';
import { Audio } from 'expo-av';
import * as FileSystem from 'expo-file-system';
import type { AudioRecordingState, RecordingStartResult, VoiceMask } from '../types';
import { configureForPlayback, configureForRecording } from '../lib/audioSession';
import {
  AUDIO_CONFIG,
  ENDPOINTS,
  MAX_RECORDING_DURATION_MS,
  uploadTimeoutMsFor,
  getApiBaseUrl,
} from '../config/api';
import { hashDeviceToken } from '../lib/deviceToken';
import { expoDownloadPort } from '../lib/maskedDownload';
import { downloadMaskedAudio, maskedDownloadUrl, maskUploadUrl } from '../lib/maskedDownloadCore';
import { deleteRecordingFile } from '../lib/recordingFiles';
import { transcriptionJobPath, type SpeechOptions } from '../lib/speechRoute';
import { transcriptionJobPort } from '../lib/transcriptionJob';
import {
  pollTranscriptionJob,
  readUploadReply,
  type JobOutcome,
  type UploadReply,
} from '../lib/transcriptionJobCore';

/** Metering readings quieter than this (dBFS) normalize to 0 amplitude — below typical mic noise floor. */
const METERING_FLOOR_DB = -60;
/** How often expo-av should push metering updates while recording, ms — fast enough for a smooth ring pulse. */
const METERING_UPDATE_INTERVAL_MS = 100;
/** Max upload attempts for transcribing a recording — 1 initial try + this many retries. */
const MAX_UPLOAD_RETRIES = 2;
/** Base delay before an upload retry, ms — doubles each attempt (500ms, 1000ms, ...). */
const UPLOAD_RETRY_BASE_DELAY_MS = 500;

/**
 * The upload exceeded its time budget.
 *
 * Distinct from `UploadHttpError` because it must not be retried: the server
 * carries on transcribing after the client gives up, so a retry only adds a
 * second full transcription of the same audio.
 */
class UploadTimeoutError extends Error {
  constructor() {
    super('Upload timed out');
    this.name = 'UploadTimeoutError';
  }
}

/** The user cancelled the upload. Never retried. */
class UploadCancelledError extends Error {
  constructor() {
    super('Upload cancelled');
    this.name = 'UploadCancelledError';
  }
}

/** HTTP error from the STT upload, carrying the status code so retry logic can tell client vs server errors apart. */
class UploadHttpError extends Error {
  constructor(
    readonly httpStatus: number,
    message: string,
  ) {
    super(message);
    this.name = 'UploadHttpError';
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Upload a recorded audio file to start a background transcription (plan 16.2)
 * and resolve with the server's reply: a job id to poll, or, from a server
 * without job mode, the transcript itself.
 * Uses XMLHttpRequest instead of fetch — fetch's RN implementation has no
 * upload-progress event, and this is the only way to drive a progress indicator.
 */
function uploadForTranscription(
  uri: string,
  deviceTokenHash: string,
  durationMs: number,
  onProgress: (fraction: number) => void,
  signal: AbortSignal,
  options: SpeechOptions = {},
): Promise<UploadReply & { kind: 'job' | 'transcript' }> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new UploadCancelledError());
      return;
    }
    const xhr = new XMLHttpRequest();
    // Scaled to the recording's length, as before job mode, so a server
    // without it still has the time to answer in full.
    xhr.timeout = uploadTimeoutMsFor(durationMs);
    xhr.open('POST', `${getApiBaseUrl()}${transcriptionJobPath(ENDPOINTS.stt, options)}`);
    xhr.setRequestHeader('X-Device-Token-Hash', deviceTokenHash);
    const onCancel = () => xhr.abort();
    signal.addEventListener('abort', onCancel);
    const settle = () => signal.removeEventListener('abort', onCancel);

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(event.loaded / event.total);
      }
    };

    xhr.onload = () => {
      settle();
      if (xhr.status >= 200 && xhr.status < 300) {
        let reply: UploadReply = { kind: 'malformed' };
        try {
          reply = readUploadReply(xhr.status, JSON.parse(xhr.responseText));
        } catch {
          reply = { kind: 'malformed' };
        }
        if (reply.kind === 'malformed') {
          reject(new UploadHttpError(xhr.status, 'Malformed transcription response'));
        } else {
          resolve(reply);
        }
        return;
      }

      let detail: string | undefined;
      try {
        detail = (JSON.parse(xhr.responseText) as { detail?: string }).detail;
      } catch {
        detail = undefined;
      }
      reject(new UploadHttpError(xhr.status, detail ?? `Upload failed (${xhr.status})`));
    };

    xhr.onerror = () => {
      settle();
      reject(new UploadHttpError(0, 'Network error during upload'));
    };
    xhr.ontimeout = () => {
      settle();
      reject(new UploadTimeoutError());
    };
    xhr.onabort = () => {
      settle();
      reject(new UploadCancelledError());
    };

    const formData = new FormData();
    formData.append('audio', {
      uri,
      name: 'confession.aac',
      type: 'audio/aac',
    } as unknown as Blob);

    xhr.send(formData);
  });
}

/**
 * Normalize a metering reading (dBFS, roughly -160 quiet to 0 loud) to 0-1.
 * Clamped to `METERING_FLOOR_DB` so normal speech uses the full range instead
 * of being crushed into the top sliver of -160..0.
 */
function normalizeMetering(db: number): number {
  const clamped = Math.max(db, METERING_FLOOR_DB);
  return (clamped - METERING_FLOOR_DB) / -METERING_FLOOR_DB;
}

export interface UseAudioRecorderOptions {
  /**
   * Ask for the microphone and switch the audio session to recording as soon as
   * the hook mounts (the booth does). Turn it off for a screen where voice is
   * optional: nothing then happens until the user starts a recording.
   */
  prepareOnMount?: boolean;
  /** Called with the file when the maximum duration stops the recording by itself. */
  onAutoStop?: (uri: string, durationMs: number) => void;
}

/**
 * Upload *uri* for masking and return the one-time download id.
 *
 * `fetch` carries no timeout of its own, so without the abort the request could
 * hang and leave the booth stuck on "Processing…" with no way forward.
 */
async function requestMaskedDownload(
  uri: string,
  mask: VoiceMask,
  durationMs: number,
  deviceTokenHash: string,
  signal: AbortSignal,
): Promise<string> {
  const formData = new FormData();
  formData.append('audio', { uri, name: 'confession.aac', type: 'audio/aac' } as unknown as Blob);
  formData.append('mask', mask);
  const abort = new AbortController();
  const abortTimer = setTimeout(() => abort.abort(), uploadTimeoutMsFor(durationMs));
  // The user's Cancel ends the request as the time budget would.
  const onCancel = () => abort.abort();
  signal.addEventListener('abort', onCancel);
  if (signal.aborted) abort.abort();
  try {
    const response = await fetch(maskUploadUrl(getApiBaseUrl()), {
      method: 'POST',
      headers: { 'X-Device-Token-Hash': deviceTokenHash },
      body: formData,
      signal: abort.signal,
    });
    if (!response.ok) {
      throw new Error(`Voice masking failed (${response.status})`);
    }
    const body = (await response.json()) as { download_id: string };
    return body.download_id;
  } finally {
    clearTimeout(abortTimer);
    signal.removeEventListener('abort', onCancel);
  }
}

/** What to tell the user when a background transcription did not produce text. */
function jobFailureMessage(outcome: Exclude<JobOutcome, { kind: 'ready' }>): string | null {
  switch (outcome.kind) {
    case 'cancelled':
      return null;
    case 'failed':
      return outcome.code === 'no_text'
        ? 'No speech was heard in the recording'
        : 'Transcription failed';
    case 'gone':
      return 'The transcription expired before it could be collected';
    case 'timed_out':
      return 'Transcription took too long';
  }
}

/**
 * Custom hook for audio recording functionality.
 * Manages the full recording lifecycle:
 * - Permission requests
 * - Recording start/stop
 * - File URI retrieval
 * - Duration tracking
 * - Error handling
 */
export function useAudioRecorder({
  prepareOnMount = true,
  onAutoStop,
}: UseAudioRecorderOptions = {}) {
  const [state, setState] = useState<AudioRecordingState>({
    isRecording: false,
    audioUri: null,
    durationMs: 0,
    hasPermission: null,
    error: null,
    amplitude: 0,
    isUploading: false,
    uploadProgress: 0,
    uploadError: null,
    transcriptionPhase: null,
  });

  const recordingRef = useRef<Audio.Recording | null>(null);
  // The files this hook has made, so a new recording or leaving the booth can
  // delete them. Only the most recent of each is ever on disk.
  const recordingFileRef = useRef<string | null>(null);
  const maskedFileRef = useRef<string | null>(null);
  // False once the hook is gone. An upload or a masking request still in flight
  // when the user leaves the booth finishes after the clean-up below has run, so
  // whatever it writes then has to be deleted on the spot.
  const isMountedRef = useRef(true);
  const durationIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Whether this hook has put the audio session into recording mode and not yet
  // handed it back, so unmounting can restore playback.
  const isSessionRecordingRef = useRef(false);
  const onAutoStopRef = useRef(onAutoStop);
  onAutoStopRef.current = onAutoStop;
  // One per transcription or masking still running, so Cancel and leaving the
  // screen can stop them: the upload, the polling and the download.
  const inFlightRef = useRef(new Set<AbortController>());

  /**
   * Request microphone permission on mount (unless `prepareOnMount` is off).
   * Also configures the audio mode for recording.
   */
  useEffect(() => {
    isMountedRef.current = true;
    const setupAudio = async () => {
      try {
        const { granted } = await Audio.requestPermissionsAsync();
        setState((prev) => ({ ...prev, hasPermission: granted }));

        if (granted) {
          await configureForRecording();
          isSessionRecordingRef.current = true;
        }
      } catch (_error: unknown) {
        setState((prev) => ({
          ...prev,
          hasPermission: false,
          error: 'Failed to initialize audio',
        }));
      }
    };

    if (prepareOnMount) void setupAudio();

    // Cleanup: stop recording if component unmounts, and delete the files it
    // made. The review screen sits on top of the booth in the stack, so the
    // booth only unmounts once that flow is over.
    return () => {
      isMountedRef.current = false;
      for (const controller of inFlightRef.current) controller.abort();
      inFlightRef.current.clear();
      const recording = recordingRef.current;
      recordingRef.current = null;
      const recordingFile = recordingFileRef.current;
      const restorePlayback = isSessionRecordingRef.current;
      isSessionRecordingRef.current = false;
      // Stop first, then delete: deleting a file the recorder is still
      // finalising would race it. Leave the audio session as playback wants it.
      void (async () => {
        if (recording) {
          try {
            await recording.stopAndUnloadAsync();
          } catch {
            // Never started, or already unloaded: nothing left to stop.
          }
        }
        await deleteRecordingFile(recordingFile);
        if (restorePlayback) {
          try {
            await configureForPlayback();
          } catch {
            // The next screen that plays audio sets the mode itself.
          }
        }
      })();
      void deleteRecordingFile(maskedFileRef.current);
      if (durationIntervalRef.current) {
        clearInterval(durationIntervalRef.current);
      }
    };
    // Mount-only by design: the option picks the mode once, it is not reactive.
  }, []);

  /**
   * Throw away a recording that was started for a booth the user has already
   * left: stop the recorder (releasing the microphone), delete its file, and
   * forget it. Used when leaving the booth overtakes the start.
   */
  const discardAbandonedRecording = useCallback(async (recording: Audio.Recording) => {
    try {
      await recording.stopAndUnloadAsync();
    } catch {
      // Never started, or already unloaded: nothing left to stop.
    }
    await deleteRecordingFile(recording.getURI());
    if (recordingRef.current === recording) {
      recordingRef.current = null;
    }
    if (durationIntervalRef.current) {
      clearInterval(durationIntervalRef.current);
      durationIntervalRef.current = null;
    }
  }, []);

  /**
   * Start recording audio.
   * Must have permission and not already be recording.
   */
  const startRecording = useCallback(async (): Promise<RecordingStartResult> => {
    try {
      if (!state.hasPermission) {
        const { granted } = await Audio.requestPermissionsAsync();
        if (!granted) {
          setState((prev) => ({
            ...prev,
            hasPermission: false,
            error: 'Microphone permission denied',
          }));
          return 'permission_denied';
        }
        setState((prev) => ({ ...prev, hasPermission: true }));
      }

      // Recording mode is set here, not only on mount, so a screen that does not
      // prepare on mount still gets it when the user taps the mic.
      // Always, not only when the ref is unset: it is idempotent, and a cleanup from a
      // previous screen can still be restoring playback after this one mounted.
      await configureForRecording();
      isSessionRecordingRef.current = true;

      // Unload any previous recording
      if (recordingRef.current) {
        await recordingRef.current.stopAndUnloadAsync();
      }
      // Recording again replaces the last take: its files are no longer needed.
      await deleteRecordingFile(recordingFileRef.current);
      await deleteRecordingFile(maskedFileRef.current);
      recordingFileRef.current = null;
      maskedFileRef.current = null;

      const recording = new Audio.Recording();
      await recording.prepareToRecordAsync({
        isMeteringEnabled: true,
        android: {
          extension: '.aac',
          outputFormat: Audio.AndroidOutputFormat.AAC_ADTS,
          audioEncoder: Audio.AndroidAudioEncoder.AAC,
          sampleRate: AUDIO_CONFIG.sampleRate,
          numberOfChannels: AUDIO_CONFIG.channels,
          bitRate: AUDIO_CONFIG.bitRate,
        },
        ios: {
          extension: '.aac',
          outputFormat: Audio.IOSOutputFormat.MPEG4AAC,
          audioQuality: Audio.IOSAudioQuality.HIGH,
          sampleRate: AUDIO_CONFIG.sampleRate,
          numberOfChannels: AUDIO_CONFIG.channels,
          bitRate: AUDIO_CONFIG.bitRate,
          linearPCMBitDepth: 16,
          linearPCMIsBigEndian: false,
          linearPCMIsFloat: false,
        },
        web: {
          mimeType: 'audio/webm',
          bitsPerSecond: AUDIO_CONFIG.bitRate,
        },
      });

      if (!isMountedRef.current) {
        // The booth was left while the recorder was being prepared.
        await discardAbandonedRecording(recording);
        return 'failed';
      }

      recordingRef.current = recording;
      // The file exists from here on, so track it now: leaving the booth while
      // recording, or a failure while stopping, must still be able to delete it.
      recordingFileRef.current = recording.getURI();

      // Live mic amplitude, for the voice-responsive ring visualization.
      recording.setProgressUpdateInterval(METERING_UPDATE_INTERVAL_MS);
      recording.setOnRecordingStatusUpdate((recordingStatus) => {
        if (typeof recordingStatus.metering !== 'number') return;
        setState((prev) => ({
          ...prev,
          amplitude: normalizeMetering(recordingStatus.metering as number),
        }));
      });

      // Track duration
      const startTime = Date.now();
      if (durationIntervalRef.current) {
        clearInterval(durationIntervalRef.current);
      }
      durationIntervalRef.current = setInterval(() => {
        const elapsed = Date.now() - startTime;
        setState((prev) => ({ ...prev, durationMs: elapsed }));

        // Auto-stop at max duration, handing the file to the caller so the
        // recording is not lost without a word.
        if (elapsed >= MAX_RECORDING_DURATION_MS) {
          if (durationIntervalRef.current) {
            clearInterval(durationIntervalRef.current);
            durationIntervalRef.current = null;
          }
          void stopRecording().then((uri) => {
            if (uri) onAutoStopRef.current?.(uri, elapsed);
          });
        }
      }, 100);

      await recording.startAsync();
      if (!isMountedRef.current) {
        await discardAbandonedRecording(recording);
        return 'failed';
      }
      setState((prev) => ({
        ...prev,
        isRecording: true,
        audioUri: null,
        error: null,
        durationMs: 0,
        amplitude: 0,
      }));
      return 'started';
    } catch (_error: unknown) {
      setState((prev) => ({
        ...prev,
        isRecording: false,
        error: 'Failed to start recording',
      }));
      return 'failed';
    }
  }, [state.hasPermission, discardAbandonedRecording]);

  /**
   * Stop recording and return the audio file URI.
   * Cleans up the recording instance and interval timer.
   */
  const stopRecording = useCallback(async (): Promise<string | null> => {
    try {
      if (!recordingRef.current) {
        return null;
      }

      await recordingRef.current.stopAndUnloadAsync();
      const uri = recordingRef.current.getURI();

      // Clean up interval
      if (durationIntervalRef.current) {
        clearInterval(durationIntervalRef.current);
        durationIntervalRef.current = null;
      }

      recordingRef.current = null;

      // Hand the session back to playback. Without this the app stays in
      // the recording configuration for the rest of its life, and the
      // review screen's "play masked audio" inherits a session still set up
      // to capture rather than play.
      await configureForPlayback();
      isSessionRecordingRef.current = false;

      if (!uri) {
        throw new Error('Recording produced no audio file');
      }

      // Get file info
      const fileInfo = await FileSystem.getInfoAsync(uri);
      if (!fileInfo.exists) {
        throw new Error('Recording file was not saved');
      }

      recordingFileRef.current = uri;
      if (!isMountedRef.current) {
        // The booth was left while this was being stopped; nobody will use it.
        void deleteRecordingFile(uri);
        return null;
      }
      setState((prev) => ({
        ...prev,
        isRecording: false,
        audioUri: uri,
        error: null,
        amplitude: 0,
      }));

      return uri;
    } catch (_error: unknown) {
      setState((prev) => ({
        ...prev,
        isRecording: false,
        error: 'Failed to stop recording',
      }));
      return null;
    }
  }, []);

  /**
   * Upload a recorded audio file for transcription and wait for the transcript,
   * retrying transient upload failures with exponential backoff.
   *
   * The upload starts a background job (plan 16.2) and the hook then polls for
   * it, so a long recording no longer holds one request open for minutes. The
   * poll survives a lost connection and the app being backgrounded; it ends when
   * the job finishes, is gone, runs past its deadline, or is cancelled with
   * `cancelProcessing`.
   *
   * The upload retries on network errors and 5xx responses (up to
   * `MAX_UPLOAD_RETRIES` extra attempts); a 4xx response means the request
   * itself is bad (empty/oversized audio, rate limit) and won't succeed on
   * retry, so it fails immediately. Returns `null` — never throws — so
   * callers can fall back to a placeholder transcript instead of losing the
   * recording the user just made.
   */
  const transcribeRecording = useCallback(async (uri: string, durationMs: number, options: SpeechOptions = {}): Promise<string | null> => {
    setState((prev) => ({
      ...prev,
      isUploading: true,
      uploadProgress: 0,
      uploadError: null,
      transcriptionPhase: 'uploading',
    }));
    const finish = (transcript: string | null, uploadError: string | null): string | null => {
      if (isMountedRef.current) {
        setState((prev) => ({
          ...prev,
          isUploading: false,
          uploadProgress: transcript === null ? prev.uploadProgress : 1,
          uploadError,
          transcriptionPhase: null,
        }));
      }
      return transcript;
    };

    let deviceTokenHash: string;
    try {
      deviceTokenHash = await hashDeviceToken();
    } catch (_error: unknown) {
      // A failed identity read must not escape: the caller shows its own message.
      return finish(null, 'Failed to upload recording');
    }

    const controller = new AbortController();
    inFlightRef.current.add(controller);
    try {
      let reply: (UploadReply & { kind: 'job' | 'transcript' }) | null = null;
      let lastError: unknown = null;
      for (let attempt = 0; attempt <= MAX_UPLOAD_RETRIES; attempt++) {
        try {
          reply = await uploadForTranscription(
            uri,
            deviceTokenHash,
            durationMs,
            (fraction) => {
              setState((prev) => ({ ...prev, uploadProgress: fraction }));
            },
            controller.signal,
            options,
          );
          break;
        } catch (error: unknown) {
          lastError = error;
          if (error instanceof UploadCancelledError || controller.signal.aborted) {
            return finish(null, null);
          }
          const isClientError = error instanceof UploadHttpError && error.httpStatus >= 400;
          // A timeout is not transient here: the server keeps working after the
          // client gives up, so each "retry" starts another full transcription
          // of the same audio while the user waits out another whole budget.
          const isTimeout = error instanceof UploadTimeoutError;
          if (isClientError || isTimeout || attempt === MAX_UPLOAD_RETRIES) {
            break;
          }
          await sleep(UPLOAD_RETRY_BASE_DELAY_MS * 2 ** attempt);
        }
      }

      if (reply === null) {
        const message = lastError instanceof Error ? lastError.message : 'Failed to upload recording';
        return finish(null, message);
      }
      if (reply.kind === 'transcript') return finish(reply.transcript, null);

      if (isMountedRef.current) {
        setState((prev) => ({ ...prev, uploadProgress: 1, transcriptionPhase: 'transcribing' }));
      }
      const outcome = await pollTranscriptionJob(
        transcriptionJobPort(deviceTokenHash),
        reply.jobId,
        controller.signal,
      );
      if (outcome.kind === 'ready') return finish(outcome.transcript, null);
      return finish(null, jobFailureMessage(outcome));
    } finally {
      inFlightRef.current.delete(controller);
    }
  }, []);

  /**
   * Stop every transcription and masking this hook has running. Each one
   * resolves `null`, as a failure would, and the recording stays on the device.
   */
  const cancelProcessing = useCallback(() => {
    for (const controller of inFlightRef.current) controller.abort();
  }, []);

  /**
   * Upload a recorded audio file to have a voice mask applied, returning a
   * local file URI to the masked WAV — or `null` if masking failed, letting
   * the caller fall back to playing the original (unmasked) recording
   * instead of losing playback entirely.
   *
   * The server holds the masked WAV for one download (plan 16.4) and
   * expo-file-system streams it straight to the cache, rather than receiving
   * it as base64 JSON held in memory (about 33.6 MB for five minutes). RN's
   * `fetch().blob()` stays avoided: it is unreliable on this stack.
   */
  const maskRecording = useCallback(
    async (uri: string, mask: VoiceMask, durationMs: number): Promise<string | null> => {
      const controller = new AbortController();
      inFlightRef.current.add(controller);
      try {
        const deviceTokenHash = await hashDeviceToken();
        const downloadId = await requestMaskedDownload(
          uri,
          mask,
          durationMs,
          deviceTokenHash,
          controller.signal,
        );
        const maskedUri = `${FileSystem.cacheDirectory}masked_${Date.now()}.wav`;
        // Tracked before the write, so a write that fails part-way is still cleaned up.
        maskedFileRef.current = maskedUri;
        const saved = await downloadMaskedAudio(
          expoDownloadPort,
          maskedDownloadUrl(getApiBaseUrl(), downloadId),
          maskedUri,
          { 'X-Device-Token-Hash': deviceTokenHash },
          uploadTimeoutMsFor(durationMs),
          controller.signal,
        );
        if (!saved || !isMountedRef.current || controller.signal.aborted) {
          void deleteRecordingFile(maskedUri);
          return null;
        }
        return maskedUri;
      } catch (_error: unknown) {
        return null;
      } finally {
        inFlightRef.current.delete(controller);
      }
    },
    [],
  );

  /**
   * Reset the recorder state to idle.
   */
  const reset = useCallback(() => {
    setState({
      isRecording: false,
      audioUri: null,
      durationMs: 0,
      hasPermission: state.hasPermission,
      error: null,
      amplitude: 0,
      isUploading: false,
      uploadProgress: 0,
      uploadError: null,
      transcriptionPhase: null,
    });
  }, [state.hasPermission]);

  return {
    ...state,
    startRecording,
    stopRecording,
    transcribeRecording,
    maskRecording,
    cancelProcessing,
    reset,
  };
}
