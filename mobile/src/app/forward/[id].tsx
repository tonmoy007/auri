// Auri — Forward screen
// Pick a recipient department for a confession that already exists in
// 'pending' status, then call the forward endpoint to route it there.

import React, { useCallback, useEffect, useState } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
  SafeAreaView,
  ActivityIndicator,
} from 'react-native';
import { router, Stack, useLocalSearchParams } from 'expo-router';
import { colors } from '../../theme/colors';
import { typography, spacing } from '../../theme';
import { ShimmerText } from '../../components/LoadingStates';
import { API_BASE_URL, ENDPOINTS } from '../../config/api';
import { useHaptics } from '../../hooks/useHaptics';
import { hashDeviceToken } from '../../lib/deviceToken';

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
 * Forward screen — choose which department a confession is routed to.
 *
 * Reached from Review after the confession has already been created
 * (status 'pending'); this screen only calls the forward endpoint,
 * it never creates the confession itself.
 */
export default function ForwardScreen(): React.JSX.Element {
  const rawParams = useLocalSearchParams();
  const id = readStringParam(rawParams, 'id') ?? '';
  const [departments, setDepartments] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [isLoadingDepartments, setIsLoadingDepartments] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const haptics = useHaptics();

  useEffect(() => {
    let cancelled = false;

    async function loadDepartments(): Promise<void> {
      try {
        const response = await fetch(`${API_BASE_URL}${ENDPOINTS.departments}`);
        if (!response.ok) {
          throw new Error(`Failed to load departments (${response.status})`);
        }
        const body = (await response.json()) as { departments: string[] };
        if (!cancelled) {
          setDepartments(body.departments);
        }
      } catch (error: unknown) {
        if (!cancelled) {
          setLoadError(
            error instanceof Error ? error.message : 'Failed to load departments',
          );
        }
      } finally {
        if (!cancelled) {
          setIsLoadingDepartments(false);
        }
      }
    }

    void loadDepartments();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSelectDepartment = useCallback(
    (department: string) => {
      haptics.selectionChanged();
      setSelected(department);
    },
    [haptics],
  );

  const handleConfirm = useCallback(async () => {
    if (selected === null) {
      return;
    }
    setIsSubmitting(true);
    setSubmitError(null);
    try {
      const deviceTokenHash = await hashDeviceToken();
      const response = await fetch(`${API_BASE_URL}${ENDPOINTS.forward(id)}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Device-Token-Hash': deviceTokenHash,
        },
        body: JSON.stringify({ department: selected }),
      });

      if (!response.ok) {
        const problem = (await response.json().catch(() => null)) as {
          detail?: string;
        } | null;
        throw new Error(problem?.detail ?? `Forward failed (${response.status})`);
      }

      haptics.success();
      router.dismissAll();
      router.replace('/');
    } catch (error: unknown) {
      setSubmitError(
        error instanceof Error ? error.message : 'Failed to forward confession',
      );
    } finally {
      setIsSubmitting(false);
    }
  }, [id, selected, haptics]);

  return (
    <SafeAreaView style={styles.container}>
      <Stack.Screen options={{ title: 'Forward', headerShown: false }} />

      <View style={styles.header}>
        <Text style={styles.title}>Choose a department</Text>
        <Text style={styles.subtitle}>
          Your identity stays hidden — only the team context is shared.
        </Text>
      </View>

      {isLoadingDepartments ? (
        <View style={styles.centerFill}>
          <ActivityIndicator color={colors.candleGlow} />
        </View>
      ) : loadError !== null ? (
        <View style={styles.centerFill}>
          <Text style={styles.errorText}>{loadError}</Text>
        </View>
      ) : (
        <ScrollView
          style={styles.scrollView}
          contentContainerStyle={styles.scrollContent}
        >
          {departments.map((department) => {
            const isSelected = department === selected;
            return (
              <TouchableOpacity
                key={department}
                style={[styles.row, isSelected && styles.rowSelected]}
                onPress={() => handleSelectDepartment(department)}
                activeOpacity={0.7}
                accessibilityRole="radio"
                accessibilityState={{ checked: isSelected }}
                accessibilityLabel={department}
              >
                <Text
                  style={[styles.rowText, isSelected && styles.rowTextSelected]}
                >
                  {department}
                </Text>
                {isSelected && <Text style={styles.checkmark}>✓</Text>}
              </TouchableOpacity>
            );
          })}
        </ScrollView>
      )}

      {submitError !== null && (
        <Text style={styles.errorText} accessibilityRole="alert">
          {submitError}
        </Text>
      )}

      <View style={styles.actionRow}>
        <TouchableOpacity
          style={styles.cancelButton}
          onPress={() => router.back()}
          activeOpacity={0.7}
          accessibilityRole="button"
          accessibilityLabel="Cancel forwarding"
        >
          <Text style={styles.cancelButtonText}>Cancel</Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={[
            styles.confirmButton,
            (selected === null || isSubmitting) && styles.buttonDisabled,
          ]}
          onPress={handleConfirm}
          activeOpacity={0.7}
          disabled={selected === null || isSubmitting}
          accessibilityRole="button"
          accessibilityLabel="Confirm forward"
        >
          {isSubmitting ? (
            <ShimmerText style={styles.confirmButtonText}>Sending…</ShimmerText>
          ) : (
            <Text style={styles.confirmButtonText}>Forward</Text>
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
  header: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.lg,
    paddingBottom: spacing.md,
  },
  title: {
    fontSize: typography.fontSize.lg,
    fontWeight: typography.fontWeight.semibold,
    color: colors.slate200,
  },
  subtitle: {
    fontSize: typography.fontSize.sm,
    color: colors.slate400,
    marginTop: spacing.xs,
  },
  centerFill: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  scrollView: {
    flex: 1,
  },
  scrollContent: {
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.xl,
  },
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: colors.slate800,
    borderRadius: 12,
    padding: spacing.md,
    borderWidth: 1,
    borderColor: colors.slate700,
    marginBottom: spacing.sm,
  },
  rowSelected: {
    borderColor: colors.candleGlow,
    backgroundColor: colors.slate700,
  },
  rowText: {
    fontSize: typography.fontSize.sm,
    color: colors.slate300,
  },
  rowTextSelected: {
    color: colors.candleGlow,
    fontWeight: typography.fontWeight.semibold,
  },
  checkmark: {
    fontSize: typography.fontSize.md,
    color: colors.candleGlow,
  },
  errorText: {
    fontSize: typography.fontSize.xs,
    color: colors.rose400,
    textAlign: 'center',
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
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
  confirmButton: {
    flex: 2,
    paddingVertical: spacing.md,
    borderRadius: 12,
    backgroundColor: colors.emerald600,
    alignItems: 'center',
    justifyContent: 'center',
  },
  confirmButtonText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.white,
  },
  buttonDisabled: {
    opacity: 0.5,
  },
});
