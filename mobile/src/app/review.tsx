// Auri — Review screen
// Shows transcript, AI summary, masked audio playback, forward/delete controls, anonymity toggle

import React, { useState, useCallback, useEffect, useRef } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
  Switch,
  SafeAreaView,
} from 'react-native';
import { router, Stack, useLocalSearchParams } from 'expo-router';
import { Audio } from 'expo-av';
import { colors } from '../theme/colors';
import { typography, spacing } from '../theme';
import { API_BASE_URL, ENDPOINTS } from '../config/api';
import { ShimmerText } from '../components/LoadingStates';
import { useHaptics } from '../hooks/useHaptics';
import { hashDeviceToken } from '../lib/deviceToken';
import type { VoiceMask } from '../types';

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
 * Review screen — allows user to review their anonymous confession
 * before submitting or discarding it.
 */
export default function ReviewScreen(): React.JSX.Element {
  const rawParams = useLocalSearchParams();
  const id = readStringParam(rawParams, 'id') ?? '';
  const audioUri = readStringParam(rawParams, 'audioUri');
  const transcriptParam = readStringParam(rawParams, 'transcript');
  const voiceMaskParam = readStringParam(rawParams, 'voiceMask') as VoiceMask | undefined;
  const [anonymityEnabled, setAnonymityEnabled] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const soundRef = useRef<Audio.Sound | null>(null);
  const haptics = useHaptics();

  // Placeholder text is shown until real transcript/summary data flows in via params.
  const transcript =
    transcriptParam ?? '… your anonymous confession transcript will appear here …';
  const summary =
    'An AI-generated summary of your confession will be displayed here after processing.';
  const voiceMask: VoiceMask = voiceMaskParam ?? 'warm';

  useEffect(() => {
    return () => {
      void soundRef.current?.unloadAsync();
    };
  }, []);

  const handleForward = useCallback(async () => {
    setIsSubmitting(true);
    setActionError(null);
    try {
      const deviceTokenHash = await hashDeviceToken();
      const response = await fetch(`${API_BASE_URL}${ENDPOINTS.confessions}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          device_token_hash: deviceTokenHash,
          voice_mask: voiceMask,
          transcript,
        }),
      });

      if (response.status !== 201) {
        const problem = (await response.json().catch(() => null)) as {
          detail?: string;
        } | null;
        throw new Error(problem?.detail ?? `Submission failed (${response.status})`);
      }

      const created = (await response.json()) as { id: string };

      if (anonymityEnabled) {
        // Fully blind — nothing more to choose, the confession stays anonymous.
        haptics.success();
        router.back();
        return;
      }

      // "Someone in your team" — the confession exists (status 'pending') but
      // still needs a department target before it can be delivered.
      router.push({
        pathname: '/forward/[id]',
        params: { id: created.id },
      });
    } catch (error: unknown) {
      setActionError(
        error instanceof Error ? error.message : 'Failed to submit confession',
      );
    } finally {
      setIsSubmitting(false);
    }
  }, [transcript, voiceMask, anonymityEnabled, haptics]);

  const handleDelete = useCallback(() => {
    router.push({
      pathname: '/delete-confirmation',
      params: { id },
    });
  }, [id]);

  const handlePlayback = useCallback(async () => {
    if (!audioUri) {
      setActionError('No recording available to play');
      return;
    }
    try {
      if (soundRef.current) {
        await soundRef.current.replayAsync();
        return;
      }
      const { sound } = await Audio.Sound.createAsync({ uri: audioUri });
      soundRef.current = sound;
      await sound.playAsync();
    } catch (_error: unknown) {
      setActionError('Failed to play masked audio');
    }
  }, [audioUri]);

  const handleToggleAnonymity = useCallback(
    (value: boolean) => {
      haptics.selectionChanged();
      setAnonymityEnabled(value);
    },
    [haptics],
  );

  return (
    <SafeAreaView style={styles.container}>
      <Stack.Screen
        options={{
          title: 'Review',
          headerShown: false,
        }}
      />

      <ScrollView
        style={styles.scrollView}
        contentContainerStyle={styles.scrollContent}
      >
        {/* Transcript section */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Transcript</Text>
          <View style={styles.card}>
            <Text style={styles.transcriptText}>{transcript}</Text>
          </View>
        </View>

        {/* AI Summary section */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>AI Summary</Text>
          <View style={styles.card}>
            <Text style={styles.summaryText}>{summary}</Text>
          </View>
        </View>

        {/* Audio playback */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Masked Audio</Text>
          <TouchableOpacity
            style={styles.playbackButton}
            onPress={handlePlayback}
            activeOpacity={0.7}
            accessibilityRole="button"
            accessibilityLabel="Play masked audio"
          >
            <Text style={styles.playbackIcon}>▶</Text>
            <Text style={styles.playbackText}>Play anonymized recording</Text>
          </TouchableOpacity>
        </View>

        {/* Anonymity toggle — identity is hidden either way; this picks whether
            a department target is attached before delivery. */}
        <View style={styles.toggleRow}>
          <View style={styles.toggleInfo}>
            <Text style={styles.toggleLabel}>
              {anonymityEnabled ? 'Fully blind' : 'Someone in your team'}
            </Text>
            <Text style={styles.toggleDescription}>
              {anonymityEnabled
                ? 'Sent with no recipient context at all'
                : 'Choose a department to route this to next'}
            </Text>
          </View>
          <Switch
            value={anonymityEnabled}
            onValueChange={handleToggleAnonymity}
            trackColor={{
              false: colors.slate700,
              true: colors.emerald600,
            }}
            thumbColor={anonymityEnabled ? colors.emerald400 : colors.slate300}
            accessibilityRole="switch"
            accessibilityLabel="Toggle between fully blind and team-context delivery"
          />
        </View>
      </ScrollView>

      {actionError !== null && (
        <Text style={styles.errorText} accessibilityRole="alert">
          {actionError}
        </Text>
      )}

      {/* Action buttons */}
      <View style={styles.actionRow}>
        <TouchableOpacity
          style={styles.deleteButton}
          onPress={handleDelete}
          activeOpacity={0.7}
          accessibilityRole="button"
          accessibilityLabel="Delete confession"
        >
          <Text style={styles.deleteButtonText}>Delete</Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={[styles.forwardButton, isSubmitting && styles.buttonDisabled]}
          onPress={handleForward}
          activeOpacity={0.7}
          disabled={isSubmitting}
          accessibilityRole="button"
          accessibilityLabel="Submit confession"
        >
          {isSubmitting ? (
            <ShimmerText style={styles.forwardButtonText}>
              Submitting…
            </ShimmerText>
          ) : (
            <Text style={styles.forwardButtonText}>
              {anonymityEnabled ? 'Send Anonymously' : 'Choose Department'}
            </Text>
          )}
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.boothDark,
  },
  scrollView: {
    flex: 1,
  },
  scrollContent: {
    padding: spacing.lg,
    paddingBottom: spacing.xxl + 80,
  },
  section: {
    marginBottom: spacing.lg,
  },
  sectionTitle: {
    fontSize: typography.fontSize.md,
    fontWeight: typography.fontWeight.semibold,
    color: colors.slate200,
    marginBottom: spacing.sm,
    letterSpacing: 1,
  },
  card: {
    backgroundColor: colors.slate800,
    borderRadius: 12,
    padding: spacing.md,
    borderWidth: 1,
    borderColor: colors.slate700,
  },
  transcriptText: {
    fontSize: typography.fontSize.sm,
    color: colors.slate300,
    lineHeight: 22,
  },
  summaryText: {
    fontSize: typography.fontSize.sm,
    color: colors.slate400,
    fontStyle: 'italic',
    lineHeight: 22,
  },
  playbackButton: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.slate800,
    borderRadius: 12,
    padding: spacing.md,
    borderWidth: 1,
    borderColor: colors.slate700,
  },
  playbackIcon: {
    fontSize: typography.fontSize.lg,
    color: colors.candleGlow,
    marginRight: spacing.md,
  },
  playbackText: {
    fontSize: typography.fontSize.sm,
    color: colors.slate300,
  },
  toggleRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: colors.slate800,
    borderRadius: 12,
    padding: spacing.md,
    borderWidth: 1,
    borderColor: colors.slate700,
  },
  toggleInfo: {
    flex: 1,
    marginRight: spacing.md,
  },
  toggleLabel: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.slate200,
  },
  toggleDescription: {
    fontSize: typography.fontSize.xs,
    color: colors.slate400,
    marginTop: 2,
  },
  actionRow: {
    flexDirection: 'row',
    padding: spacing.lg,
    gap: spacing.md,
    borderTopWidth: 1,
    borderTopColor: colors.slate800,
  },
  deleteButton: {
    flex: 1,
    paddingVertical: spacing.md,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: colors.rose600,
    alignItems: 'center',
    justifyContent: 'center',
  },
  deleteButtonText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.rose400,
  },
  forwardButton: {
    flex: 2,
    paddingVertical: spacing.md,
    borderRadius: 12,
    backgroundColor: colors.emerald600,
    alignItems: 'center',
    justifyContent: 'center',
  },
  forwardButtonText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.white,
  },
  buttonDisabled: {
    opacity: 0.5,
  },
  errorText: {
    fontSize: typography.fontSize.xs,
    color: colors.rose400,
    textAlign: 'center',
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
  },
});
