// Auri — Guide intro
// Shown before the first use of the Guide, and again whenever the server's
// disclaimer version changes. "I understand" stores the accepted version.

import React, { useCallback, useState } from 'react';
import {
  ActivityIndicator,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { router, Stack } from 'expo-router';
import { colors } from '../../theme/colors';
import { borderRadius, spacing, typography } from '../../theme';
import { useHaptics } from '../../hooks/useHaptics';
import { usePriestStatus } from '../../hooks/usePriestStatus';
import { acknowledgePriestIntro } from '../../hooks/useSettings';
import { INTRO_STATEMENTS, presentError } from '../../lib/priestPresentation';

const MIN_TOUCH_TARGET = 44;

/** First-use screen: what the Guide is and is not, privacy, and help if you need it now. */
export default function PriestIntroScreen(): React.JSX.Element {
  const { status, error, isLoading, reload } = usePriestStatus();
  const haptics = useHaptics();
  const [isSaving, setIsSaving] = useState(false);

  const handleAccept = useCallback(async () => {
    if (!status) return;
    setIsSaving(true);
    try {
      await acknowledgePriestIntro(status.disclaimer_version);
      haptics.success();
      router.replace('/priest');
    } finally {
      setIsSaving(false);
    }
  }, [status, haptics]);

  const unavailable = status && !status.enabled ? presentError({ code: 'disabled', retryAfterSeconds: null }) : null;
  const failure = error ? presentError(error) : unavailable;
  const canAccept = status !== null && status.enabled && !isSaving;

  return (
    <SafeAreaView style={styles.container}>
      <Stack.Screen options={{ headerShown: false }} />
      <View style={styles.header}>
        <TouchableOpacity
          style={styles.backButton}
          onPress={() => router.back()}
          accessibilityRole="button"
          accessibilityLabel="Go back"
        >
          <Text style={styles.backButtonText}>‹</Text>
        </TouchableOpacity>
      </View>
      <ScrollView contentContainerStyle={styles.content}>
        <Text style={styles.title} accessibilityRole="header">
          {status ? `Before you ask ${status.persona_name}` : 'Before you ask'}
        </Text>
        {INTRO_STATEMENTS.map((statement) => (
          <View key={statement.key} style={styles.card}>
            <Text style={styles.cardHeading} accessibilityRole="header">
              {statement.heading}
            </Text>
            <Text style={styles.cardBody}>{statement.body}</Text>
          </View>
        ))}
        {isLoading ? <ActivityIndicator color={colors.candleGlow} accessibilityLabel="Loading" /> : null}
        {failure ? (
          <View accessibilityRole="alert">
            <Text style={styles.failure}>{failure.message}</Text>
            {failure.retryable ? (
              <TouchableOpacity
                style={styles.secondaryButton}
                onPress={() => void reload()}
                accessibilityRole="button"
                accessibilityLabel="Try again"
              >
                <Text style={styles.secondaryButtonText}>Try again</Text>
              </TouchableOpacity>
            ) : null}
          </View>
        ) : null}
      </ScrollView>
      <View style={styles.footer}>
        <TouchableOpacity
          style={[styles.primaryButton, !canAccept && styles.primaryButtonDisabled]}
          onPress={() => void handleAccept()}
          disabled={!canAccept}
          accessibilityRole="button"
          accessibilityLabel="I understand"
          accessibilityState={{ disabled: !canAccept }}
        >
          <Text style={styles.primaryButtonText}>I understand</Text>
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
  header: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
  },
  backButton: {
    width: MIN_TOUCH_TARGET,
    height: MIN_TOUCH_TARGET,
    justifyContent: 'center',
  },
  backButtonText: {
    fontSize: typography.fontSize.xxl,
    color: colors.candleGlow,
  },
  content: {
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.xl,
  },
  title: {
    marginBottom: spacing.lg,
    fontSize: typography.fontSize.xl,
    fontWeight: typography.fontWeight.bold,
    color: colors.slate200,
  },
  card: {
    marginBottom: spacing.md,
    padding: spacing.lg,
    borderRadius: borderRadius.md,
    backgroundColor: colors.slate800,
  },
  cardHeading: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.candleGlow,
    textTransform: 'uppercase',
    letterSpacing: 1,
  },
  cardBody: {
    marginTop: spacing.sm,
    fontSize: typography.fontSize.md,
    lineHeight: 24,
    color: colors.slate200,
  },
  failure: {
    marginTop: spacing.md,
    fontSize: typography.fontSize.sm,
    color: colors.rose400,
  },
  footer: {
    padding: spacing.lg,
  },
  primaryButton: {
    minHeight: MIN_TOUCH_TARGET,
    paddingVertical: spacing.md,
    borderRadius: borderRadius.full,
    backgroundColor: colors.candleGlow,
    alignItems: 'center',
    justifyContent: 'center',
  },
  primaryButtonDisabled: {
    backgroundColor: colors.slate700,
  },
  primaryButtonText: {
    fontSize: typography.fontSize.lg,
    fontWeight: typography.fontWeight.semibold,
    color: colors.boothDark,
  },
  secondaryButton: {
    minHeight: MIN_TOUCH_TARGET,
    marginTop: spacing.sm,
    borderRadius: borderRadius.md,
    borderWidth: 1.5,
    borderColor: colors.candleGlow,
    alignItems: 'center',
    justifyContent: 'center',
  },
  secondaryButtonText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.candleGlow,
  },
});
