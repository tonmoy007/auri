// Auri — useAudioRecorder hook
// Custom hook wrapping expo-av for audio recording with permission management

import { useState, useCallback, useRef, useEffect } from 'react';
import { Audio, InterruptionModeAndroid, InterruptionModeIOS } from 'expo-av';
import * as FileSystem from 'expo-file-system';
import type { AudioRecordingState, VoiceMask } from '../types';
import {
  API_BASE_URL,
  AUDIO_CONFIG,
  ENDPOINTS,
  MAX_RECORDING_DURATION_MS,
  REQUEST_TIMEOUT_MS,
} from '../config/api';
import { hashDeviceToken } from '../lib/deviceToken';

/** Metering readings quieter than this (dBFS) normalize to 0 amplitude — below typical mic noise floor. */
const METERING_FLOOR_DB = -60;
/** How often expo-av should push metering updates while recording, ms — fast enough for a smooth ring pulse. */
const METERING_UPDATE_INTERVAL_MS = 100;
/** Max upload attempts for transcribing a recording — 1 initial try + this many retries. */
const MAX_UPLOAD_RETRIES = 2;
/** Base delay before an upload retry, ms — doubles each attempt (500ms, 1000ms, ...). */
const UPLOAD_RETRY_BASE_DELAY_MS = 500;

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
  onProgress: (fraction: number) => void,
): Promise<string> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.timeout = REQUEST_TIMEOUT_MS;
    xhr.open('POST', `${API_BASE_URL}${ENDPOINTS.stt}`);
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
    xhr.ontimeout = () => reject(new UploadHttpError(0, 'Upload timed out'));

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

/**
 * Custom hook for audio recording functionality.
 * Manages the full recording lifecycle:
 * - Permission requests
 * - Recording start/stop
 * - File URI retrieval
 * - Duration tracking
 * - Error handling
 */
export function useAudioRecorder() {
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
  const durationIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  /**
   * Request microphone permission on mount.
   * Also configures the audio mode for recording.
   */
  useEffect(() => {
    const setupAudio = async () => {
      try {
        const { granted } = await Audio.requestPermissionsAsync();
        setState((prev) => ({ ...prev, hasPermission: granted }));

        if (granted) {
          await Audio.setAudioModeAsync({
            allowsRecordingIOS: true,
            playsInSilentModeIOS: true,
            staysActiveInBackground: false,
            interruptionModeIOS: InterruptionModeIOS.DuckOthers,
            interruptionModeAndroid: InterruptionModeAndroid.DuckOthers,
            shouldDuckAndroid: true,
            playThroughEarpieceAndroid: false,
          });
        }
      } catch (_error: unknown) {
        setState((prev) => ({
          ...prev,
          hasPermission: false,
          error: 'Failed to initialize audio',
        }));
      }
    };

    void setupAudio();

    // Cleanup: stop recording if component unmounts
    return () => {
      if (recordingRef.current) {
        void recordingRef.current.stopAndUnloadAsync();
        recordingRef.current = null;
      }
      if (durationIntervalRef.current) {
        clearInterval(durationIntervalRef.current);
      }
    };
  }, []);

  /**
   * Start recording audio.
   * Must have permission and not already be recording.
   */
  const startRecording = useCallback(async () => {
    try {
      if (!state.hasPermission) {
        const { granted } = await Audio.requestPermissionsAsync();
        if (!granted) {
          setState((prev) => ({
            ...prev,
            error: 'Microphone permission denied',
          }));
          return;
        }
        setState((prev) => ({ ...prev, hasPermission: true }));
      }

      // Unload any previous recording
      if (recordingRef.current) {
        await recordingRef.current.stopAndUnloadAsync();
      }

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

      recordingRef.current = recording;

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
      durationIntervalRef.current = setInterval(() => {
        const elapsed = Date.now() - startTime;
        setState((prev) => ({ ...prev, durationMs: elapsed }));

        // Auto-stop at max duration
        if (elapsed >= MAX_RECORDING_DURATION_MS) {
          void stopRecording();
        }
      }, 100);

      await recording.startAsync();
      setState((prev) => ({
        ...prev,
        isRecording: true,
        audioUri: null,
        error: null,
        durationMs: 0,
        amplitude: 0,
      }));
    } catch (_error: unknown) {
      setState((prev) => ({
        ...prev,
        isRecording: false,
        error: 'Failed to start recording',
      }));
    }
  }, [state.hasPermission]);

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

      if (!uri) {
        throw new Error('Recording produced no audio file');
      }

      // Get file info
      const fileInfo = await FileSystem.getInfoAsync(uri);
      if (!fileInfo.exists) {
        throw new Error('Recording file was not saved');
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
  const transcribeRecording = useCallback(async (uri: string): Promise<string | null> => {
    setState((prev) => ({ ...prev, isUploading: true, uploadProgress: 0, uploadError: null }));

    const deviceTokenHash = await hashDeviceToken();
    let lastError: unknown = null;

    for (let attempt = 0; attempt <= MAX_UPLOAD_RETRIES; attempt++) {
      try {
        const transcript = await uploadForTranscription(uri, deviceTokenHash, (fraction) => {
          setState((prev) => ({ ...prev, uploadProgress: fraction }));
        });
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
        if (isClientError || attempt === MAX_UPLOAD_RETRIES) {
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
    async (uri: string, mask: VoiceMask): Promise<string | null> => {
      try {
        const deviceTokenHash = await hashDeviceToken();
        const formData = new FormData();
        formData.append('audio', {
          uri,
          name: 'confession.aac',
          type: 'audio/aac',
        } as unknown as Blob);
        formData.append('mask', mask);

        const response = await fetch(`${API_BASE_URL}${ENDPOINTS.voiceMask}`, {
          method: 'POST',
          headers: { 'X-Device-Token-Hash': deviceTokenHash },
          body: formData,
        });
        if (!response.ok) {
          throw new Error(`Voice masking failed (${response.status})`);
        }
        const body = (await response.json()) as { audio_base64: string };

        const maskedUri = `${FileSystem.cacheDirectory}masked_${Date.now()}.wav`;
        await FileSystem.writeAsStringAsync(maskedUri, body.audio_base64, {
          encoding: FileSystem.EncodingType.Base64,
        });
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
