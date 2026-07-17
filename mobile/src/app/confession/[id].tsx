// Auri — Confession booth screen
// 3D scene with record button, voice mask selector, and status display

import React, { useState, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Dimensions,
  Pressable,
  SafeAreaView,
} from 'react-native';
import { router, Stack, useLocalSearchParams } from 'expo-router';
import { colors } from '../../theme/colors';
import { typography, spacing } from '../../theme';
import { ConfessionBooth } from '../../components/ConfessionBooth';
import { RecordButton } from '../../components/RecordButton';
import { VoiceMaskSelector } from '../../components/VoiceMaskSelector';
import { useAudioRecorder } from '../../hooks/useAudioRecorder';
import type { VoiceMask, ConfessionStatus, Environment } from '../../types';

const { height } = Dimensions.get('window');

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
  const [voiceMask, setVoiceMask] = useState<VoiceMask>('ethereal');
  const [environment, setEnvironment] = useState<Environment>('classic');
  const [status, setStatus] = useState<ConfessionStatus>('idle');
  const recorder = useAudioRecorder();

  const handleStartRecording = useCallback(async () => {
    setStatus('recording');
    try {
      await recorder.startRecording();
    } catch (_error: unknown) {
      setStatus('idle');
    }
  }, [recorder]);

  const handleStopRecording = useCallback(async () => {
    setStatus('processing');
    try {
      const audioUri = await recorder.stopRecording();
      if (!audioUri) {
        setStatus('idle');
        return;
      }
      setStatus('done');
      router.push({
        pathname: '/review',
        params: { id, audioUri, voiceMask },
      });
    } catch (_error: unknown) {
      setStatus('idle');
    }
  }, [recorder, id, voiceMask]);

  const handleToggleEnvironment = useCallback(() => {
    setEnvironment((prev: Environment) => {
      const environments: Environment[] = ['classic', 'forest', 'rooftop'];
      const nextIndex = (environments.indexOf(prev) + 1) % environments.length;
      const next = environments[nextIndex];
      if (!next) return prev;
      return next;
    });
  }, []);

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
        <ConfessionBooth environment={environment} />
      </Pressable>

      {/* Status overlay */}
      <View style={styles.statusBar}>
        <Text style={styles.statusText}>{statusMessages[status]}</Text>
        <Text style={styles.voiceMaskLabel}>
          Mask: {voiceMask.charAt(0).toUpperCase() + voiceMask.slice(1)}
        </Text>
      </View>

      {/* Voice mask selector */}
      <View style={styles.voiceMaskContainer}>
        <VoiceMaskSelector
          selected={voiceMask}
          onSelect={setVoiceMask}
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

      {/* Environment toggle hint */}
      <Text style={styles.environmentHint}>
        Tap the booth to change environment
      </Text>
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
    position: 'absolute',
    top: 60,
    left: spacing.lg,
    right: spacing.lg,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    zIndex: 10,
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
    top: height * 0.3,
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
    bottom: 100,
    left: 0,
    right: 0,
    textAlign: 'center',
    fontSize: typography.fontSize.xs,
    color: colors.slate500,
    zIndex: 10,
  },
});
