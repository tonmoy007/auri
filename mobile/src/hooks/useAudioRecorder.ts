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
import { deleteRecordingFile } from '../lib/recordingFiles';
import { speechToTextPath, type SpeechOptions } from '../lib/speechRoute';

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
 * Upload a recorded audio file to the STT endpoint and resolve with its transcript.
 * Uses XMLHttpRequest instead of fetch — fetch's RN implementation has no
 * upload-progress event, and this is the only way to drive a progress indicator.
 */
function uploadForTranscription(
  uri: string,
  deviceTokenHash: string,
  durationMs: number,
  onProgress: (fraction: number) => void,
  options: SpeechOptions = {},
): Promise<string> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    // Scaled to the recording's length: transcription is slower than
    // realtime, so a flat budget fails long confessions by construction.
    xhr.timeout = uploadTimeoutMsFor(durationMs);
    xhr.open('POST', `${getApiBaseUrl()}${speechToTextPath(ENDPOINTS.stt, options)}`);
    xhr.setRequestHeader('X-Device-Token-Hash', deviceTokenHash);

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(event.loaded / event.total);
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const body = JSON.parse(xhr.responseText) as { transcript: string };
          resolve(body.transcript);
        } catch {
          reject(new UploadHttpError(xhr.status, 'Malformed transcription response'));
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

    xhr.onerror = () => reject(new UploadHttpError(0, 'Network error during upload'));
    xhr.ontimeout = () => reject(new UploadTimeoutError());

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
      if (!isSessionRecordingRef.current) {
        await configureForRecording();
        isSessionRecordingRef.current = true;
      }

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
   * Upload a recorded audio file for transcription, retrying transient
   * failures with exponential backoff.
   *
   * Retries on network errors, timeouts, and 5xx responses (up to
   * `MAX_UPLOAD_RETRIES` extra attempts); a 4xx response means the request
   * itself is bad (empty/oversized audio, rate limit) and won't succeed on
   * retry, so it fails immediately. Returns `null` — never throws — so
   * callers can fall back to a placeholder transcript instead of losing the
   * recording the user just made.
   */
  const transcribeRecording = useCallback(async (uri: string, durationMs: number, options: SpeechOptions = {}): Promise<string | null> => {
    setState((prev) => ({ ...prev, isUploading: true, uploadProgress: 0, uploadError: null }));

    let deviceTokenHash: string;
    try {
      deviceTokenHash = await hashDeviceToken();
    } catch (_error: unknown) {
      // A failed identity read must not escape: the caller shows its own message.
      setState((prev) => ({
        ...prev,
        isUploading: false,
        uploadError: 'Failed to upload recording',
      }));
      return null;
    }
    let lastError: unknown = null;

    for (let attempt = 0; attempt <= MAX_UPLOAD_RETRIES; attempt++) {
      try {
        const transcript = await uploadForTranscription(
          uri,
          deviceTokenHash,
          durationMs,
          (fraction) => {
            setState((prev) => ({ ...prev, uploadProgress: fraction }));
          },
          options,
        );
        setState((prev) => ({
          ...prev,
          isUploading: false,
          uploadProgress: 1,
          uploadError: null,
        }));
        return transcript;
      } catch (error: unknown) {
        lastError = error;
        const isClientError = error instanceof UploadHttpError && error.httpStatus >= 400;
        // A timeout is not transient here: the server keeps transcribing
        // after the client gives up, so each "retry" starts another full
        // transcription of the same audio while the user waits out another
        // whole budget for a result that was never going to arrive sooner.
        const isTimeout = error instanceof UploadTimeoutError;
        if (isClientError || isTimeout || attempt === MAX_UPLOAD_RETRIES) {
          break;
        }
        await sleep(UPLOAD_RETRY_BASE_DELAY_MS * 2 ** attempt);
      }
    }

    const message = lastError instanceof Error ? lastError.message : 'Failed to upload recording';
    setState((prev) => ({ ...prev, isUploading: false, uploadError: message }));
    return null;
  }, []);

  /**
   * Upload a recorded audio file to have a voice mask applied, returning a
   * local file URI to the masked WAV — or `null` if masking failed, letting
   * the caller fall back to playing the original (unmasked) recording
   * instead of losing playback entirely.
   *
   * Uses `fetch` + base64 rather than the STT upload's XHR/blob approach:
   * RN's `fetch().blob()` handling is unreliable on this stack (new
   * architecture/Fabric), and the backend returns base64 JSON specifically
   * to sidestep that — see `backend/app/api/v1/voice.py`.
   */
  const maskRecording = useCallback(
    async (uri: string, mask: VoiceMask, durationMs: number): Promise<string | null> => {
      try {
        const deviceTokenHash = await hashDeviceToken();
        const formData = new FormData();
        formData.append('audio', {
          uri,
          name: 'confession.aac',
          type: 'audio/aac',
        } as unknown as Blob);
        formData.append('mask', mask);

        // `fetch` carries no timeout of its own, so without this the masking
        // request could hang indefinitely and leave the booth stuck on
        // "Processing…" with no way forward.
        const abort = new AbortController();
        const abortTimer = setTimeout(() => abort.abort(), uploadTimeoutMsFor(durationMs));
        let response: Response;
        try {
          response = await fetch(`${getApiBaseUrl()}${ENDPOINTS.voiceMask}`, {
            method: 'POST',
            headers: { 'X-Device-Token-Hash': deviceTokenHash },
            body: formData,
            signal: abort.signal,
          });
        } finally {
          clearTimeout(abortTimer);
        }
        if (!response.ok) {
          throw new Error(`Voice masking failed (${response.status})`);
        }
        const body = (await response.json()) as { audio_base64: string };

        const maskedUri = `${FileSystem.cacheDirectory}masked_${Date.now()}.wav`;
        // Tracked before the write, so a write that fails part-way is still cleaned up.
        maskedFileRef.current = maskedUri;
        await FileSystem.writeAsStringAsync(maskedUri, body.audio_base64, {
          encoding: FileSystem.EncodingType.Base64,
        });
        if (!isMountedRef.current) {
          void deleteRecordingFile(maskedUri);
          return null;
        }
        return maskedUri;
      } catch (_error: unknown) {
        return null;
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
    });
  }, [state.hasPermission]);

  return {
    ...state,
    startRecording,
    stopRecording,
    transcribeRecording,
    maskRecording,
    reset,
  };
}
