// Auri — Review screen
// Shows transcript, AI summary, masked audio playback, forward/delete controls, anonymity toggle

import React, { useState, useCallback } from 'react';
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
import { colors } from '../theme/colors';
import { typography, spacing } from '../theme';

/**
 * Review screen — allows user to review their anonymous confession
 * before submitting or discarding it.
 */
export default function ReviewScreen(): React.JSX.Element {
  const { id } = useLocalSearchParams<{ id: string }>();
  const [anonymityEnabled, setAnonymityEnabled] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Placeholder data — will be replaced with real data from WebSocket/API
  const transcript = '… your anonymous confession transcript will appear here …';
  const summary =
    'An AI-generated summary of your confession will be displayed here after processing.';

  const handleForward = useCallback(async () => {
    setIsSubmitting(true);
    // TODO: Submit confession via API
    // await api.submitConfession(id, { anonymityEnabled });
    setIsSubmitting(false);
    router.back();
  }, [id, anonymityEnabled]);

  const handleDelete = useCallback(() => {
    // TODO: Delete recording and navigate home
    router.replace('/');
  }, []);

  const handlePlayback = useCallback(() => {
    // TODO: Play masked audio via expo-av
  }, []);

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

        {/* Anonymity toggle */}
        <View style={styles.toggleRow}>
          <View style={styles.toggleInfo}>
            <Text style={styles.toggleLabel}>Anonymous posting</Text>
            <Text style={styles.toggleDescription}>
              Your identity remains completely hidden
            </Text>
          </View>
          <Switch
            value={anonymityEnabled}
            onValueChange={setAnonymityEnabled}
            trackColor={{
              false: colors.slate700,
              true: colors.emerald600,
            }}
            thumbColor={anonymityEnabled ? colors.emerald400 : colors.slate300}
            accessibilityRole="switch"
            accessibilityLabel="Toggle anonymity"
          />
        </View>
      </ScrollView>

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
          <Text style={styles.forwardButtonText}>
            {isSubmitting ? 'Submitting…' : 'Forward Anonymously'}
          </Text>
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
});
