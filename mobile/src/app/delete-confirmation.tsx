// Auri — Delete confirmation screen
// Confirms a destructive delete before it happens, then plays a candle
// extinguish animation while the delete request completes.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, TouchableOpacity, StyleSheet, SafeAreaView } from 'react-native';
import { router, Stack, useLocalSearchParams } from 'expo-router';
import { colors } from '../theme/colors';
import { typography, spacing } from '../theme';
import { ConfessionBooth } from '../components/ConfessionBooth';
import { ThreeCanvas } from '../components/ThreeCanvas';
import { API_BASE_URL, ENDPOINTS } from '../config/api';
import { useHaptics } from '../hooks/useHaptics';
import { hashDeviceToken } from '../lib/deviceToken';

const EXTINGUISH_DURATION_MS = 1400;

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

type Stage = 'confirming' | 'extinguishing';

/**
 * Delete confirmation screen — requires an explicit second tap before a
 * confession is deleted, then plays a candle-extinguish animation while
 * the delete request is in flight so the destructive action reads as
 * final rather than instant/silent.
 */
export default function DeleteConfirmationScreen(): React.JSX.Element {
  const rawParams = useLocalSearchParams();
  const id = readStringParam(rawParams, 'id') ?? '';
  const [stage, setStage] = useState<Stage>('confirming');
  const [extinguishProgress, setExtinguishProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const animationFrameRef = useRef<number | null>(null);
  const haptics = useHaptics();

  useEffect(() => {
    return () => {
      if (animationFrameRef.current !== null) {
        cancelAnimationFrame(animationFrameRef.current);
      }
    };
  }, []);

  const runExtinguishAnimation = useCallback((onDone: () => void) => {
    const start = Date.now();
    const tick = (): void => {
      const elapsed = Date.now() - start;
      const progress = Math.min(1, elapsed / EXTINGUISH_DURATION_MS);
      setExtinguishProgress(progress);
      if (progress < 1) {
        animationFrameRef.current = requestAnimationFrame(tick);
      } else {
        onDone();
      }
    };
    animationFrameRef.current = requestAnimationFrame(tick);
  }, []);

  const handleConfirmDelete = useCallback(async () => {
    haptics.warning();
    setStage('extinguishing');
    setError(null);

    const deletePromise = (async () => {
      try {
        const deviceTokenHash = await hashDeviceToken();
        await fetch(`${API_BASE_URL}${ENDPOINTS.deleteConfession(id)}`, {
          method: 'DELETE',
          headers: { 'X-Device-Token-Hash': deviceTokenHash },
        });
      } catch (deleteError: unknown) {
        setError(
          deleteError instanceof Error
            ? deleteError.message
            : 'Failed to delete confession',
        );
      }
    })();

    runExtinguishAnimation(() => {
      void deletePromise.finally(() => {
        router.dismissAll();
        router.replace('/');
      });
    });
  }, [id, haptics, runExtinguishAnimation]);

  const handleCancel = useCallback(() => {
    router.back();
  }, []);

  return (
    <SafeAreaView style={styles.container}>
      <Stack.Screen options={{ title: 'Delete', headerShown: false }} />

      <View style={styles.threeContainer}>
        <ThreeCanvas>
          <ConfessionBooth
            environment="classic"
            extinguishProgress={extinguishProgress}
          />
        </ThreeCanvas>
      </View>

      <View style={styles.overlay}>
        <Text style={styles.title}>
          {stage === 'confirming' ? 'Delete this confession?' : 'Extinguishing…'}
        </Text>
        <Text style={styles.subtitle}>
          {stage === 'confirming'
            ? 'This cannot be undone. The recording and transcript will be permanently removed.'
            : 'Your candle is going out.'}
        </Text>

        {error !== null && (
          <Text style={styles.errorText} accessibilityRole="alert">
            {error}
          </Text>
        )}

        {stage === 'confirming' && (
          <View style={styles.actionRow}>
            <TouchableOpacity
              style={styles.cancelButton}
              onPress={handleCancel}
              activeOpacity={0.7}
              accessibilityRole="button"
              accessibilityLabel="Cancel delete"
            >
              <Text style={styles.cancelButtonText}>Keep it</Text>
            </TouchableOpacity>

            <TouchableOpacity
              style={styles.deleteButton}
              onPress={handleConfirmDelete}
              activeOpacity={0.7}
              accessibilityRole="button"
              accessibilityLabel="Confirm delete"
            >
              <Text style={styles.deleteButtonText}>Delete</Text>
            </TouchableOpacity>
          </View>
        )}
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
  title: {
    fontSize: typography.fontSize.lg,
    fontWeight: typography.fontWeight.semibold,
    color: colors.slate200,
    textAlign: 'center',
  },
  subtitle: {
    fontSize: typography.fontSize.sm,
    color: colors.slate400,
    textAlign: 'center',
    marginTop: spacing.sm,
  },
  errorText: {
    fontSize: typography.fontSize.xs,
    color: colors.rose400,
    textAlign: 'center',
    paddingTop: spacing.sm,
  },
  actionRow: {
    flexDirection: 'row',
    gap: spacing.md,
    marginTop: spacing.xl,
  },
  cancelButton: {
    flex: 1,
    paddingVertical: spacing.md,
    borderRadius: 12,
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
  deleteButton: {
    flex: 1,
    paddingVertical: spacing.md,
    borderRadius: 12,
    backgroundColor: colors.rose600,
    alignItems: 'center',
    justifyContent: 'center',
  },
  deleteButtonText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.white,
  },
});
