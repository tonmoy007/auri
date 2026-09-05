// Auri — Confession booth screen
// 3D scene with record button, voice mask selector, and status display

import React, { useState, useCallback, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Pressable,
  SafeAreaView,
} from 'react-native';
import { router, Stack, useLocalSearchParams } from 'expo-router';
import { colors } from '../../theme/colors';
import { typography, spacing } from '../../theme';
import { ConfessionBooth } from '../../components/ConfessionBooth';
import { ShimmerText } from '../../components/LoadingStates';
import { RecordButton } from '../../components/RecordButton';
import { ThreeCanvas } from '../../components/ThreeCanvas';
import { VoiceMaskSelector } from '../../components/VoiceMaskSelector';
import { useAudioRecorder } from '../../hooks/useAudioRecorder';
import { useHaptics } from '../../hooks/useHaptics';
import { useSettings } from '../../hooks/useSettings';
import type { VoiceMask, ConfessionStatus, Environment } from '../../types';

/** Delay before the door starts swinging open on entry, ms — lets the fade-in overlay clear first. */
const DOOR_OPEN_DELAY_MS = 150;
/** How long the door close animation needs before it's safe to navigate away, ms. */
const DOOR_CLOSE_MS = 550;

/**
 * Extract a single string param from expo-router's raw params object.
 *
 * expo-router types local search params as `Record<string, string | string[]>`
 * (repeated query keys become arrays); this app only ever sends single values.
 */
function readStringParam(
  rawParams: Record<string, string | string[]>,
  key: string,
): string | undefined {
  const value = rawParams[key];
  return typeof value === 'string' ? value : undefined;
}

/**
 * Confession booth screen — the core recording experience.
 * Manages recording state, voice mask selection, and status feedback.
 */
export default function ConfessionScreen(): React.JSX.Element {
  const rawParams = useLocalSearchParams();
  const id = readStringParam(rawParams, 'id') ?? '';
  const { defaultVoiceMask, defaultEnvironment, isLoaded } = useSettings();
  const [voiceMask, setVoiceMask] = useState<VoiceMask>(defaultVoiceMask);
  const [environment, setEnvironment] = useState<Environment>(defaultEnvironment);
  const [status, setStatus] = useState<ConfessionStatus>('idle');
  const [doorOpen, setDoorOpen] = useState(false);
  const [isExiting, setIsExiting] = useState(false);
  const recorder = useAudioRecorder();
  const haptics = useHaptics();

  // Swing the door open shortly after mounting — the entry animation.
  useEffect(() => {
    const timer = setTimeout(() => setDoorOpen(true), DOOR_OPEN_DELAY_MS);
    return () => clearTimeout(timer);
  }, []);

  // Seed live selection from the persisted Settings defaults once they load.
  // Deliberately keyed on `isLoaded` alone (not the values) so this fires
  // once on load-completion and never fights the user's in-screen picks.
  useEffect(() => {
    if (isLoaded) {
      setVoiceMask(defaultVoiceMask);
      setEnvironment(defaultEnvironment);
    }
  }, [isLoaded]);

  const handleStartRecording = useCallback(async () => {
    setStatus('recording');
    haptics.recordStart();
    try {
      await recorder.startRecording();
    } catch (_error: unknown) {
      setStatus('idle');
    }
  }, [recorder, haptics]);

  const handleStopRecording = useCallback(async () => {
    setStatus('processing');
    haptics.recordStop();
    try {
      const audioUri = await recorder.stopRecording();
      if (!audioUri) {
        setStatus('idle');
        return;
      }
      // Transcribe and mask in parallel while still "processing" — review.tsx
      // shows an explicit error state if the transcript comes back null after
      // retries, and falls back to playing the original (unmasked) recording
      // if masking fails, rather than losing playback entirely.
      const [transcript, maskedAudioUri] = await Promise.all([
        recorder.transcribeRecording(audioUri),
        recorder.maskRecording(audioUri, voiceMask),
      ]);
      setStatus('done');
      // Swing the door shut before leaving the booth — the exit animation.
      setDoorOpen(false);
      await new Promise((resolve) => setTimeout(resolve, DOOR_CLOSE_MS));
      router.push({
        pathname: '/review',
        params: transcript
          ? { id, audioUri: maskedAudioUri ?? audioUri, voiceMask, transcript }
          : { id, audioUri: maskedAudioUri ?? audioUri, voiceMask },
      });
    } catch (_error: unknown) {
      setStatus('idle');
    }
  }, [recorder, id, voiceMask, haptics]);

  /**
   * Leave the booth without submitting.
   *
   * Always available, including mid-recording: this is a confession booth,
   * and someone who wants out must be able to get out. Nothing has been
   * sent at this point, so an abandoned recording is simply discarded —
   * `useAudioRecorder` stops and unloads the hardware recording on unmount.
   * The door closes on the way out, matching the exit choreography the
   * submit path already uses.
   */
  const handleExitBooth = useCallback(async () => {
    if (isExiting) return;
    setIsExiting(true);
    haptics.selectionChanged();
    setDoorOpen(false);
    await new Promise((resolve) => setTimeout(resolve, DOOR_CLOSE_MS));
    // Reached directly via a deep link there is nothing to pop back to, so
    // fall back to the landing screen rather than stranding the user here.
    if (router.canGoBack()) {
      router.back();
    } else {
      router.replace('/');
    }
  }, [isExiting, haptics]);

  const handleToggleEnvironment = useCallback(() => {
    haptics.selectionChanged();
    setEnvironment((prev: Environment) => {
      const environments: Environment[] = ['classic', 'forest', 'rooftop'];
      const nextIndex = (environments.indexOf(prev) + 1) % environments.length;
      const next = environments[nextIndex];
      if (!next) return prev;
      return next;
    });
  }, [haptics]);

  const handleSelectVoiceMask = useCallback(
    (mask: VoiceMask) => {
      haptics.selectionChanged();
      setVoiceMask(mask);
    },
    [haptics],
  );

  const statusMessages: Record<ConfessionStatus, string> = {
    idle: 'Speak freely',
    recording: 'Recording…',
    processing: 'Anonymizing…',
    done: 'Ready for review',
  };

  return (
    <SafeAreaView style={styles.container}>
      <Stack.Screen
        options={{
          title: 'Confession',
          headerShown: false,
        }}
      />

      {/* 3D booth scene — tapping it cycles the environment */}
      <Pressable
        style={styles.threeContainer}
        onPress={handleToggleEnvironment}
        accessibilityRole="button"
        accessibilityLabel="Change booth environment"
      >
        <ThreeCanvas environment={environment} isRecording={status === 'recording'}>
          <ConfessionBooth
            environment={environment}
            isProcessing={status === 'processing'}
            doorOpen={doorOpen}
            amplitude={status === 'recording' ? recorder.amplitude : 0}
          />
        </ThreeCanvas>
      </Pressable>

      {/* Status overlay — the back control lives here rather than floating
          on its own, so every persistent control stays in the one top
          cluster this screen already groups them into. */}
      <View style={styles.statusBar}>
        <Pressable
          onPress={handleExitBooth}
          disabled={isExiting}
          accessibilityRole="button"
          accessibilityLabel="Leave the booth"
          accessibilityHint="Discards this recording without sending it"
          style={styles.backButton}
        >
          <Text style={styles.backButtonText}>‹</Text>
        </Pressable>

        {status === 'processing' ? (
          <ShimmerText style={styles.statusText}>
            {recorder.isUploading
              ? `Anonymizing… ${Math.round(recorder.uploadProgress * 100)}%`
              : statusMessages[status]}
          </ShimmerText>
        ) : (
          <Text style={styles.statusText}>{statusMessages[status]}</Text>
        )}
        <Text style={styles.voiceMaskLabel}>
          Mask: {voiceMask.charAt(0).toUpperCase() + voiceMask.slice(1)}
        </Text>
      </View>

      {/* Environment toggle hint — grouped with the top status cluster so it
          never competes with the record button's own "Tap to stop" label. */}
      <Text style={styles.environmentHint}>
        Tap the booth to change environment
      </Text>

      {/* Voice mask selector — anchored under the hint instead of floating
          mid-screen, so all controls live in one predictable top cluster. */}
      <View style={styles.voiceMaskContainer}>
        <VoiceMaskSelector
          selected={voiceMask}
          onSelect={handleSelectVoiceMask}
          disabled={status === 'recording' || status === 'processing'}
        />
      </View>

      {/* Record button */}
      <View style={styles.recordContainer}>
        <RecordButton
          status={status}
          onStart={handleStartRecording}
          onStop={handleStopRecording}
        />
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.boothDark,
  },
  threeContainer: {
    flex: 1,
    zIndex: 0,
  },
  statusBar: {
    // 52, not 60: the row is now as tall as the 44pt back-button target, so
    // this keeps the status text at roughly its original height and leaves a
    // gap above the environment hint at 104 instead of butting against it.
    position: 'absolute',
    top: 52,
    left: spacing.lg,
    right: spacing.lg,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    zIndex: 10,
  },
  // 44x44 is the iOS HIG minimum touch target (AGENTS.md §6.1). The chevron
  // glyph itself is small, so the tappable box is sized rather than padded.
  backButton: {
    width: 44,
    height: 44,
    justifyContent: 'center',
    alignItems: 'center',
    marginLeft: -spacing.sm,
  },
  backButtonText: {
    fontSize: typography.fontSize.xxl,
    color: colors.candleGlow,
    lineHeight: typography.fontSize.xxl,
  },
  statusText: {
    fontSize: typography.fontSize.sm,
    color: colors.slate300,
    letterSpacing: 1,
  },
  voiceMaskLabel: {
    fontSize: typography.fontSize.sm,
    color: colors.candleGlow,
  },
  voiceMaskContainer: {
    position: 'absolute',
    top: 148,
    left: 0,
    right: 0,
    zIndex: 10,
    paddingHorizontal: spacing.md,
  },
  recordContainer: {
    position: 'absolute',
    bottom: spacing.xxl,
    left: 0,
    right: 0,
    alignItems: 'center',
    zIndex: 10,
  },
  environmentHint: {
    position: 'absolute',
    top: 104,
    left: 0,
    right: 0,
    textAlign: 'center',
    fontSize: typography.fontSize.xs,
    color: colors.slate500,
    zIndex: 10,
  },
});
