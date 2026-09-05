// Auri — Review screen
// Shows transcript, AI summary, masked audio playback, forward/delete controls, anonymity toggle

import React, { useState, useCallback, useEffect, useRef } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
  SafeAreaView,
} from 'react-native';
import { router, Stack, useLocalSearchParams } from 'expo-router';
import { Audio } from 'expo-av';
import { configureForPlayback } from '../lib/audioSession';
import { colors } from '../theme/colors';
import { typography, spacing } from '../theme';
import { ENDPOINTS, getApiBaseUrl } from '../config/api';
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
  const anonymityParam = readStringParam(rawParams, 'anonymityEnabled');
  const [anonymityEnabled, setAnonymityEnabled] = useState(anonymityParam !== '0');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [hasFinishedPlaying, setHasFinishedPlaying] = useState(false);
  const [summary, setSummary] = useState<string | null>(null);
  const [isSummaryLoading, setIsSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const soundRef = useRef<Audio.Sound | null>(null);
  const haptics = useHaptics();

  // STT can fail or transcribe silence to an empty string — either way there's
  // no real transcript to show or submit. Falling back to a placeholder
  // *string* here (as this used to) meant that placeholder text got sent to
  // the backend as the actual confession on submit; now the caller is told
  // explicitly there's nothing to send.
  const hasTranscript = (transcriptParam ?? '').trim().length > 0;
  const transcript = hasTranscript ? (transcriptParam as string) : '';
  const voiceMask: VoiceMask = voiceMaskParam ?? 'warm';

  useEffect(() => {
    return () => {
      void soundRef.current?.unloadAsync();
    };
  }, []);

  // Fetch a real AI-generated summary once a real transcript exists — this
  // used to be a static placeholder string shown unconditionally.
  useEffect(() => {
    if (!hasTranscript) return;
    let cancelled = false;
    setIsSummaryLoading(true);
    setSummaryError(null);
    (async () => {
      try {
        const response = await fetch(`${getApiBaseUrl()}${ENDPOINTS.confessionPreview}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ transcript }),
        });
        if (!response.ok) {
          throw new Error(`Summary generation failed (${response.status})`);
        }
        const body = (await response.json()) as { ai_summary: string | null };
        if (!cancelled) {
          setSummary(body.ai_summary);
        }
      } catch (error: unknown) {
        if (!cancelled) {
          setSummaryError(
            error instanceof Error ? error.message : 'Failed to generate summary',
          );
        }
      } finally {
        if (!cancelled) {
          setIsSummaryLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [hasTranscript, transcript]);

  // The Anonymity screen returns here via router.replace with an updated
  // param rather than a fresh mount, so the choice made there needs to be
  // synced into local state explicitly.
  useEffect(() => {
    if (anonymityParam !== undefined) {
      setAnonymityEnabled(anonymityParam !== '0');
    }
  }, [anonymityParam]);

  const handleForward = useCallback(async () => {
    if (!hasTranscript) {
      setActionError('No transcript to submit — go back and record again.');
      return;
    }
    setIsSubmitting(true);
    setActionError(null);
    try {
      const deviceTokenHash = await hashDeviceToken();
      const response = await fetch(`${getApiBaseUrl()}${ENDPOINTS.confessions}`, {
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

      const created = (await response.json()) as {
        id: string;
        counselor_response: string | null;
      };

      haptics.success();
      // A brief, deliberate AI response stands between "submitted" and
      // "gone" — the confessor sees they were heard before the flow
      // continues into anonymity routing.
      router.push({
        pathname: '/response',
        params: {
          id: created.id,
          counselorResponse: created.counselor_response ?? '',
          anonymityEnabled: anonymityEnabled ? '1' : '0',
        },
      });
    } catch (error: unknown) {
      setActionError(
        error instanceof Error ? error.message : 'Failed to submit confession',
      );
    } finally {
      setIsSubmitting(false);
    }
  }, [hasTranscript, transcript, voiceMask, anonymityEnabled, haptics]);

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
    if (isPlaying) return;
    try {
      if (soundRef.current) {
        // Explicitly rewind before replaying — without this a second tap
        // after playback finished silently no-op'd on some Android builds
        // instead of restarting from the beginning.
        await soundRef.current.setPositionAsync(0);
        await soundRef.current.playAsync();
        setIsPlaying(true);
        setHasFinishedPlaying(false);
        return;
      }
      // The booth restores this on stop, but the review screen is also
      // reachable directly (deep link, or a resumed app), so don't assume
      // the session is already configured for playback.
      await configureForPlayback();
      const { sound } = await Audio.Sound.createAsync({ uri: audioUri });
      soundRef.current = sound;
      sound.setOnPlaybackStatusUpdate((playbackStatus) => {
        if (!playbackStatus.isLoaded) return;
        setIsPlaying(playbackStatus.isPlaying);
        if (playbackStatus.didJustFinish) {
          setIsPlaying(false);
          setHasFinishedPlaying(true);
        }
      });
      setIsPlaying(true);
      setHasFinishedPlaying(false);
      await sound.playAsync();
    } catch (error: unknown) {
      setIsPlaying(false);
      // Surface the real reason instead of swallowing it (AGENTS.md §15.1).
      // A bare "Failed to play masked audio" gave neither the user nor the
      // logs anything to act on.
      const reason = error instanceof Error ? error.message : String(error);
      setActionError(`Failed to play masked audio: ${reason}`);
    }
  }, [audioUri, isPlaying]);

  const handleOpenAnonymityChoice = useCallback(() => {
    haptics.selectionChanged();
    router.push({
      pathname: '/anonymity',
      params: {
        id,
        ...(audioUri ? { audioUri } : {}),
        ...(transcriptParam ? { transcript: transcriptParam } : {}),
        voiceMask,
        anonymityEnabled: anonymityEnabled ? '1' : '0',
      },
    });
  }, [id, audioUri, transcriptParam, voiceMask, anonymityEnabled, haptics]);

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
            {hasTranscript ? (
              <Text style={styles.transcriptText}>{transcript}</Text>
            ) : (
              <Text style={styles.transcriptMissingText}>
                We couldn't transcribe this recording. Go back and record again.
              </Text>
            )}
          </View>
        </View>

        {/* AI Summary section */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>AI Summary</Text>
          <View style={styles.card}>
            {!hasTranscript ? (
              <Text style={styles.summaryText}>Waiting on a transcript first.</Text>
            ) : isSummaryLoading ? (
              <ShimmerText style={styles.summaryText}>
                Reading between the lines…
              </ShimmerText>
            ) : summaryError !== null ? (
              <Text style={styles.summaryText}>
                Summary unavailable — you can still submit.
              </Text>
            ) : (
              <Text style={styles.summaryText}>
                {summary ?? 'No summary available.'}
              </Text>
            )}
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
            accessibilityLabel={isPlaying ? 'Playing masked audio' : 'Play masked audio'}
          >
            <Text style={styles.playbackIcon}>{isPlaying ? '⏸' : '▶'}</Text>
            <Text style={styles.playbackText}>
              {isPlaying
                ? 'Playing anonymized recording…'
                : hasFinishedPlaying
                  ? 'Replay anonymized recording'
                  : 'Play anonymized recording'}
            </Text>
          </TouchableOpacity>
        </View>

        {/* Anonymity choice — identity is hidden either way; this picks whether
            a department target is attached before delivery. Opens a dedicated
            screen with a visual preview of both modes. */}
        <TouchableOpacity
          style={styles.toggleRow}
          onPress={handleOpenAnonymityChoice}
          activeOpacity={0.7}
          accessibilityRole="button"
          accessibilityLabel="Change anonymity choice"
        >
          <View
            style={[
              styles.toggleDot,
              { backgroundColor: anonymityEnabled ? colors.emerald400 : colors.candleGlow },
            ]}
          />
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
          <Text style={styles.toggleChevron}>Change ›</Text>
        </TouchableOpacity>
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
          style={[
            styles.forwardButton,
            (isSubmitting || !hasTranscript) && styles.buttonDisabled,
          ]}
          onPress={handleForward}
          activeOpacity={0.7}
          disabled={isSubmitting || !hasTranscript}
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
  transcriptMissingText: {
    fontSize: typography.fontSize.sm,
    color: colors.rose400,
    fontStyle: 'italic',
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
  toggleDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    marginRight: spacing.md,
  },
  toggleInfo: {
    flex: 1,
    marginRight: spacing.md,
  },
  toggleChevron: {
    fontSize: typography.fontSize.sm,
    color: colors.slate500,
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
