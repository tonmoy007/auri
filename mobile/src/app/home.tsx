// Auri — Home screen: confession history
// Shows past confessions for this device with their delivery status.
// Reached from the landing screen's history icon.

import React, { useState, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  SafeAreaView,
  FlatList,
  TouchableOpacity,
  RefreshControl,
} from 'react-native';
import { router, Stack } from 'expo-router';
import { colors } from '../theme/colors';
import { typography, spacing, borderRadius } from '../theme';
import { ENDPOINTS, getApiBaseUrl } from '../config/api';
import { hashDeviceToken } from '../lib/deviceToken';

type ApiConfessionStatus = 'pending' | 'forwarded' | 'deleted' | 'flagged';

interface ConfessionHistoryItem {
  id: string;
  status: ApiConfessionStatus;
  category: string | null;
  recipient_dept: string | null;
  delivered_at: string | null;
  created_at: string;
}

interface StatusPresentation {
  label: string;
  color: string;
}

/** Maps a confession's backend state to what the history list shows. */
function describeStatus(item: ConfessionHistoryItem): StatusPresentation {
  if (item.status === 'flagged') {
    return { label: 'Under review', color: colors.rose400 };
  }
  if (item.status === 'forwarded') {
    return item.delivered_at
      ? { label: 'Delivered', color: colors.emerald400 }
      : { label: 'Forwarded · awaiting delivery', color: colors.candleGlow };
  }
  return { label: 'Pending', color: colors.slate400 };
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  return date.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

/**
 * History screen — lists this device's past confessions, newest first,
 * with a status badge showing pending / forwarded / delivered / flagged.
 */
export default function HomeScreen(): React.JSX.Element {
  const [items, setItems] = useState<ConfessionHistoryItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadHistory = useCallback(async () => {
    setLoadError(null);
    try {
      const deviceTokenHash = await hashDeviceToken();
      const response = await fetch(`${getApiBaseUrl()}${ENDPOINTS.confessions}`, {
        headers: { 'X-Device-Token-Hash': deviceTokenHash },
      });
      if (!response.ok) {
        throw new Error(`Could not load history (${response.status})`);
      }
      const body = (await response.json()) as ConfessionHistoryItem[];
      setItems(body);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : 'Could not load history');
    }
  }, []);

  useEffect(() => {
    loadHistory().finally(() => setIsLoading(false));
  }, [loadHistory]);

  const handleRefresh = useCallback(() => {
    setIsRefreshing(true);
    loadHistory().finally(() => setIsRefreshing(false));
  }, [loadHistory]);

  const handleBack = useCallback(() => {
    router.back();
  }, []);

  return (
    <SafeAreaView style={styles.container}>
      <Stack.Screen options={{ title: 'History', headerShown: false }} />

      <View style={styles.header}>
        <TouchableOpacity
          onPress={handleBack}
          accessibilityRole="button"
          accessibilityLabel="Go back"
          hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}
        >
          <Text style={styles.backIcon}>‹</Text>
        </TouchableOpacity>
        <Text style={styles.title}>Your Confessions</Text>
        <View style={styles.backIcon} />
      </View>

      {isLoading ? (
        <View style={styles.centeredState}>
          <Text style={styles.stateText}>Loading history…</Text>
        </View>
      ) : loadError ? (
        <View style={styles.centeredState}>
          <Text style={styles.stateText}>{loadError}</Text>
          <TouchableOpacity
            style={styles.retryButton}
            onPress={handleRefresh}
            accessibilityRole="button"
            accessibilityLabel="Retry loading history"
          >
            <Text style={styles.retryButtonText}>Retry</Text>
          </TouchableOpacity>
        </View>
      ) : items.length === 0 ? (
        <View style={styles.centeredState}>
          <Text style={styles.stateText}>No confessions yet.</Text>
        </View>
      ) : (
        <FlatList
          data={items}
          keyExtractor={(item) => item.id}
          contentContainerStyle={styles.listContent}
          refreshControl={
            <RefreshControl
              refreshing={isRefreshing}
              onRefresh={handleRefresh}
              tintColor={colors.candleGlow}
            />
          }
          renderItem={({ item }) => {
            const statusInfo = describeStatus(item);
            return (
              <View style={styles.card}>
                <View style={styles.cardHeader}>
                  <Text style={styles.cardCategory}>
                    {item.category ?? 'Uncategorized'}
                  </Text>
                  <Text style={styles.cardDate}>{formatDate(item.created_at)}</Text>
                </View>
                <View style={styles.statusRow}>
                  <View style={[styles.statusDot, { backgroundColor: statusInfo.color }]} />
                  <Text style={[styles.statusLabel, { color: statusInfo.color }]}>
                    {statusInfo.label}
                  </Text>
                  {item.recipient_dept ? (
                    <Text style={styles.recipientText}> · {item.recipient_dept}</Text>
                  ) : null}
                </View>
              </View>
            );
          }}
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.boothDark,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.md,
    paddingBottom: spacing.sm,
  },
  backIcon: {
    fontSize: typography.fontSize.xxl,
    color: colors.slate300,
    width: 32,
  },
  title: {
    fontSize: typography.fontSize.lg,
    fontWeight: typography.fontWeight.semibold,
    color: colors.slate200,
  },
  centeredState: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: spacing.xl,
    gap: spacing.md,
  },
  stateText: {
    fontSize: typography.fontSize.sm,
    color: colors.slate400,
    textAlign: 'center',
  },
  retryButton: {
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.lg,
    borderRadius: borderRadius.md,
    backgroundColor: colors.slate800,
    borderWidth: 1,
    borderColor: colors.slate600,
  },
  retryButtonText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.slate200,
  },
  listContent: {
    padding: spacing.lg,
    gap: spacing.md,
  },
  card: {
    backgroundColor: colors.slate800,
    borderRadius: borderRadius.lg,
    padding: spacing.lg,
    borderWidth: 1,
    borderColor: colors.slate700,
    marginBottom: spacing.md,
  },
  cardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  cardCategory: {
    fontSize: typography.fontSize.md,
    fontWeight: typography.fontWeight.semibold,
    color: colors.slate200,
    textTransform: 'capitalize',
  },
  cardDate: {
    fontSize: typography.fontSize.xs,
    color: colors.slate500,
  },
  statusRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: spacing.sm,
  },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: borderRadius.full,
    marginRight: spacing.sm,
  },
  statusLabel: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.medium,
  },
  recipientText: {
    fontSize: typography.fontSize.sm,
    color: colors.slate400,
  },
});
