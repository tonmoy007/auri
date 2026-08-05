// Auri — useAudioRecorder hook
// Custom hook wrapping expo-av for audio recording with permission management

import { useState, useCallback, useRef, useEffect } from 'react';
import { Audio, InterruptionModeAndroid, InterruptionModeIOS } from 'expo-av';
import * as FileSystem from 'expo-file-system';
import type { AudioRecordingState } from '../types';
import { AUDIO_CONFIG, MAX_RECORDING_DURATION_MS } from '../config/api';

/** Metering readings quieter than this (dBFS) normalize to 0 amplitude — below typical mic noise floor. */
const METERING_FLOOR_DB = -60;
/** How often expo-av should push metering updates while recording, ms — fast enough for a smooth ring pulse. */
const METERING_UPDATE_INTERVAL_MS = 100;

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
    });
  }, [state.hasPermission]);

  return {
    ...state,
    startRecording,
    stopRecording,
    reset,
  };
}
