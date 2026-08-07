// Auri — Anonymity choice screen
// Dedicated visual preview of "Fully blind" vs "Someone in your team" before
// confirming — reached by tapping the choice row on Review. This app has no
// cross-screen shared state; the selection flows back via route params, the
// same pattern confession -> review -> forward already uses.

import React, { useState, useCallback } from 'react';
import { View, Text, TouchableOpacity, StyleSheet, SafeAreaView } from 'react-native';
import { router, Stack, useLocalSearchParams } from 'expo-router';
import { colors } from '../theme/colors';
import { typography, spacing, borderRadius } from '../theme';
import { useHaptics } from '../hooks/useHaptics';

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
 * Anonymity choice screen.
 * Shows both delivery modes side by side with a short preview of what each
 * means, then hands the choice back to Review via a param-carrying replace.
 */
export default function AnonymityScreen(): React.JSX.Element {
  const rawParams = useLocalSearchParams();
  const id = readStringParam(rawParams, 'id') ?? '';
  const audioUri = readStringParam(rawParams, 'audioUri');
  const transcript = readStringParam(rawParams, 'transcript');
  const voiceMask = readStringParam(rawParams, 'voiceMask');
  const initialEnabled = readStringParam(rawParams, 'anonymityEnabled') !== '0';
  const [selected, setSelected] = useState(initialEnabled);
  const haptics = useHaptics();

  const handleSelect = useCallback(
    (value: boolean) => {
      haptics.selectionChanged();
      setSelected(value);
    },
    [haptics],
  );

  const handleConfirm = useCallback(() => {
    router.replace({
      pathname: '/review',
      params: {
        id,
        ...(audioUri ? { audioUri } : {}),
        ...(transcript ? { transcript } : {}),
        ...(voiceMask ? { voiceMask } : {}),
        anonymityEnabled: selected ? '1' : '0',
      },
    });
  }, [id, audioUri, transcript, voiceMask, selected]);

  const handleCancel = useCallback(() => {
    router.back();
  }, []);

  return (
    <SafeAreaView style={styles.container}>
      <Stack.Screen options={{ title: 'Anonymity', headerShown: false }} />

      <View style={styles.content}>
        <Text style={styles.title}>Who can see this?</Text>
        <Text style={styles.subtitle}>
          Your identity stays hidden either way — this only chooses whether
          the confession carries a recipient context.
        </Text>

        <TouchableOpacity
          style={[styles.card, selected && styles.cardSelected]}
          onPress={() => handleSelect(true)}
          activeOpacity={0.8}
          accessibilityRole="radio"
          accessibilityState={{ checked: selected }}
          accessibilityLabel="Fully blind — sent with no recipient context"
        >
          <View style={[styles.previewDot, { backgroundColor: colors.emerald400 }]} />
          <View style={styles.cardText}>
            <Text style={styles.cardTitle}>Fully blind</Text>
            <Text style={styles.cardDescription}>
              Sent with no recipient context at all. Nothing more to choose —
              this is the whole flow.
            </Text>
          </View>
        </TouchableOpacity>

        <TouchableOpacity
          style={[styles.card, !selected && styles.cardSelected]}
          onPress={() => handleSelect(false)}
          activeOpacity={0.8}
          accessibilityRole="radio"
          accessibilityState={{ checked: !selected }}
          accessibilityLabel="Someone in your team — choose a department to route this to"
        >
          <View style={[styles.previewDot, { backgroundColor: colors.candleGlow }]} />
          <View style={styles.cardText}>
            <Text style={styles.cardTitle}>Someone in your team</Text>
            <Text style={styles.cardDescription}>
              Still anonymous — but after submitting, you'll pick a
              department to route this to next.
            </Text>
          </View>
        </TouchableOpacity>
      </View>

      <View style={styles.actionRow}>
        <TouchableOpacity
          style={styles.cancelButton}
          onPress={handleCancel}
          activeOpacity={0.7}
          accessibilityRole="button"
          accessibilityLabel="Cancel"
        >
          <Text style={styles.cancelButtonText}>Cancel</Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={styles.confirmButton}
          onPress={handleConfirm}
          activeOpacity={0.7}
          accessibilityRole="button"
          accessibilityLabel="Confirm choice"
        >
          <Text style={styles.confirmButtonText}>Confirm</Text>
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
  content: {
    flex: 1,
    padding: spacing.lg,
    justifyContent: 'center',
    gap: spacing.md,
  },
  title: {
    fontSize: typography.fontSize.xl,
    fontWeight: typography.fontWeight.semibold,
    color: colors.slate200,
    textAlign: 'center',
  },
  subtitle: {
    fontSize: typography.fontSize.sm,
    color: colors.slate400,
    textAlign: 'center',
    marginBottom: spacing.md,
  },
  card: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    backgroundColor: colors.slate800,
    borderRadius: borderRadius.lg,
    padding: spacing.lg,
    borderWidth: 1,
    borderColor: colors.slate700,
  },
  cardSelected: {
    borderColor: colors.candleGlow,
    backgroundColor: colors.slate700,
  },
  previewDot: {
    width: 14,
    height: 14,
    borderRadius: borderRadius.full,
    marginTop: 4,
    marginRight: spacing.md,
  },
  cardText: {
    flex: 1,
  },
  cardTitle: {
    fontSize: typography.fontSize.md,
    fontWeight: typography.fontWeight.semibold,
    color: colors.slate200,
  },
  cardDescription: {
    fontSize: typography.fontSize.xs,
    color: colors.slate400,
    marginTop: 2,
    lineHeight: 18,
  },
  actionRow: {
    flexDirection: 'row',
    padding: spacing.lg,
    gap: spacing.md,
    borderTopWidth: 1,
    borderTopColor: colors.slate800,
  },
  cancelButton: {
    flex: 1,
    paddingVertical: spacing.md,
    borderRadius: borderRadius.md,
    borderWidth: 1,
    borderColor: colors.slate600,
    alignItems: 'center',
    justifyContent: 'center',
  },
  cancelButtonText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.slate300,
  },
  confirmButton: {
    flex: 2,
    paddingVertical: spacing.md,
    borderRadius: borderRadius.md,
    backgroundColor: colors.emerald600,
    alignItems: 'center',
    justifyContent: 'center',
  },
  confirmButtonText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.white,
  },
});
