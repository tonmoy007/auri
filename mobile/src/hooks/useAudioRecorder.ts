// Auri — useAudioRecorder hook
// Custom hook wrapping expo-av for audio recording with permission management

import { useState, useCallback, useRef, useEffect } from 'react';
import { Audio, InterruptionModeAndroid, InterruptionModeIOS } from 'expo-av';
import * as FileSystem from 'expo-file-system';
import type { AudioRecordingState } from '../types';
import { AUDIO_CONFIG, MAX_RECORDING_DURATION_MS } from '../config/api';

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
        void recordingRef.current.stopAndUnloadRecording();
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
        await recordingRef.current.stopAndUnloadRecording();
      }

      const recording = new Audio.Recording();
      await recording.prepareToRecordAsync({
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
          linerPCMBitDepth: 16,
          linerPCMIsBigEndian: false,
          linerPCMIsFloat: false,
        },
        web: {
          mimeType: 'audio/webm',
          bitsPerSecond: AUDIO_CONFIG.bitRate,
        },
      });

      recordingRef.current = recording;

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

      await recordingRef.current.stopAndUnloadRecording();
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
    });
  }, [state.hasPermission]);

  return {
    ...state,
    startRecording,
    stopRecording,
    reset,
  };
}
