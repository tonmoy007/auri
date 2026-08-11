// Auri — Response screen
// Shows a deliberate, compassionate AI response after a confession is
// submitted, modeled on how a confessor listens: acknowledge, then reply.

import React, { useCallback } from 'react';
import { View, Text, TouchableOpacity, StyleSheet, SafeAreaView } from 'react-native';
import { router, Stack, useLocalSearchParams } from 'expo-router';
import { colors } from '../theme/colors';
import { typography, spacing } from '../theme';
import { ConfessionBooth } from '../components/ConfessionBooth';
import { ThreeCanvas } from '../components/ThreeCanvas';
import { useHaptics } from '../hooks/useHaptics';

/** Shown if the backend's counselor response param arrives empty — the
 * confession itself still saved fine, only this reply degraded. */
const FALLBACK_RESPONSE =
  "Thank you for trusting this space with what you carried in. Whatever it is, you don't have to hold it alone — it has been heard.";

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
 * Response screen — the confessor's booth visit doesn't end at "submitted".
 * A short, compassionate AI reply stands between submission and whatever
 * comes next (fully anonymous exit, or picking a department to forward to).
 */
export default function ResponseScreen(): React.JSX.Element {
  const rawParams = useLocalSearchParams();
  const id = readStringParam(rawParams, 'id') ?? '';
  const counselorResponseParam = readStringParam(rawParams, 'counselorResponse');
  const anonymityParam = readStringParam(rawParams, 'anonymityEnabled');
  const anonymityEnabled = anonymityParam !== '0';
  const haptics = useHaptics();

  const response =
    counselorResponseParam !== undefined && counselorResponseParam.trim().length > 0
      ? counselorResponseParam
      : FALLBACK_RESPONSE;

  const handleContinue = useCallback(() => {
    haptics.selectionChanged();
    if (anonymityEnabled) {
      // Fully blind — nothing more to choose, exit the booth entirely.
      router.dismissAll();
      router.replace('/');
      return;
    }
    // "Someone in your team" — still needs a department target.
    router.replace({
      pathname: '/forward/[id]',
      params: { id },
    });
  }, [anonymityEnabled, id, haptics]);

  return (
    <SafeAreaView style={styles.container}>
      <Stack.Screen options={{ title: 'You Are Heard', headerShown: false }} />

      <View style={styles.threeContainer}>
        <ThreeCanvas>
          <ConfessionBooth environment="classic" />
        </ThreeCanvas>
      </View>

      <View style={styles.overlay}>
        <Text style={styles.eyebrow}>Your confession has been received</Text>

        <View style={styles.card}>
          <Text style={styles.responseText}>{response}</Text>
        </View>

        <TouchableOpacity
          style={styles.continueButton}
          onPress={handleContinue}
          activeOpacity={0.8}
          accessibilityRole="button"
          accessibilityLabel="Continue"
        >
          <Text style={styles.continueButtonText}>
            {anonymityEnabled ? 'Done' : 'Choose Department'}
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
  threeContainer: {
    flex: 1,
  },
  overlay: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    padding: spacing.lg,
    paddingBottom: spacing.xxl,
  },
  eyebrow: {
    fontSize: typography.fontSize.xs,
    color: colors.slate400,
    textAlign: 'center',
    letterSpacing: 1,
    textTransform: 'uppercase',
    marginBottom: spacing.md,
  },
  card: {
    backgroundColor: colors.slate800,
    borderRadius: 12,
    padding: spacing.lg,
    borderWidth: 1,
    borderColor: colors.slate700,
  },
  responseText: {
    fontSize: typography.fontSize.md,
    color: colors.slate200,
    lineHeight: 24,
    fontStyle: 'italic',
    textAlign: 'center',
  },
  continueButton: {
    marginTop: spacing.xl,
    paddingVertical: spacing.md,
    borderRadius: 12,
    backgroundColor: colors.candleGlow,
    alignItems: 'center',
    justifyContent: 'center',
  },
  continueButtonText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.boothDark,
    letterSpacing: 1,
    textTransform: 'uppercase',
  },
});
