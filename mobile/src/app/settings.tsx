// Auri — Settings screen
// Every user-facing preference in one place: default voice mask, default
// booth environment, anonymous identity management, and app info.

import React, { useState, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
  SafeAreaView,
  Alert,
} from 'react-native';
import { router, Stack } from 'expo-router';
import Constants from 'expo-constants';
import { colors } from '../theme/colors';
import { typography, spacing, borderRadius } from '../theme';
import { VoiceMaskSelector } from '../components/VoiceMaskSelector';
import { useHaptics } from '../hooks/useHaptics';
import { useSettings } from '../hooks/useSettings';
import {
  getDeviceIdentityReference,
  resetDeviceToken,
} from '../lib/deviceToken';
import type { Environment, VoiceMask } from '../types';

const ENVIRONMENT_OPTIONS: { id: Environment; label: string }[] = [
  { id: 'classic', label: 'Classic Booth' },
  { id: 'forest', label: 'Forest Glade' },
  { id: 'rooftop', label: 'Rooftop Night' },
];

/**
 * Settings screen — Voice & Booth defaults, Privacy & Identity, About.
 */
export default function SettingsScreen(): React.JSX.Element {
  const {
    defaultVoiceMask,
    defaultEnvironment,
    setDefaultVoiceMask,
    setDefaultEnvironment,
  } = useSettings();
  const [identityRef, setIdentityRef] = useState<string | null>(null);
  const [isResetting, setIsResetting] = useState(false);
  const haptics = useHaptics();

  useEffect(() => {
    let cancelled = false;
    getDeviceIdentityReference().then((ref) => {
      if (!cancelled) setIdentityRef(ref);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleResetIdentity = useCallback(() => {
    Alert.alert(
      'Reset Anonymous Identity?',
      'This creates a brand new anonymous identity. Confessions you already ' +
        'sent under your current identity will no longer be accessible from ' +
        'this device. This cannot be undone.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Reset',
          style: 'destructive',
          onPress: async () => {
            setIsResetting(true);
            try {
              const newRef = await resetDeviceToken();
              setIdentityRef(newRef);
              haptics.success();
            } finally {
              setIsResetting(false);
            }
          },
        },
      ],
    );
  }, [haptics]);

  const handleSelectVoiceMask = useCallback(
    (mask: VoiceMask) => {
      haptics.selectionChanged();
      setDefaultVoiceMask(mask);
    },
    [haptics, setDefaultVoiceMask],
  );

  const handleSelectEnvironment = useCallback(
    (environment: Environment) => {
      haptics.selectionChanged();
      setDefaultEnvironment(environment);
    },
    [haptics, setDefaultEnvironment],
  );

  const appVersion = Constants.expoConfig?.version ?? '0.0.0';

  return (
    <SafeAreaView style={styles.container}>
      <Stack.Screen options={{ headerShown: false }} />

      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity
          onPress={() => router.back()}
          accessibilityRole="button"
          accessibilityLabel="Go back"
          style={styles.backButton}
        >
          <Text style={styles.backButtonText}>‹</Text>
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Settings</Text>
        <View style={styles.backButton} />
      </View>

      <ScrollView contentContainerStyle={styles.scrollContent}>
        {/* Voice & Booth */}
        <Text style={styles.sectionTitle}>Voice & Booth</Text>

        <Text style={styles.fieldLabel}>Default Voice Mask</Text>
        <VoiceMaskSelector
          selected={defaultVoiceMask}
          onSelect={handleSelectVoiceMask}
        />

        <Text style={[styles.fieldLabel, styles.fieldLabelSpaced]}>
          Default Environment
        </Text>
        <View style={styles.environmentRow}>
          {ENVIRONMENT_OPTIONS.map((option) => {
            const isSelected = defaultEnvironment === option.id;
            return (
              <TouchableOpacity
                key={option.id}
                style={[
                  styles.environmentOption,
                  isSelected && styles.environmentOptionSelected,
                ]}
                onPress={() => handleSelectEnvironment(option.id)}
                accessibilityRole="radio"
                accessibilityState={{ selected: isSelected }}
                accessibilityLabel={`${option.label} environment`}
              >
                <Text
                  style={[
                    styles.environmentOptionText,
                    isSelected && styles.environmentOptionTextSelected,
                  ]}
                >
                  {option.label}
                </Text>
              </TouchableOpacity>
            );
          })}
        </View>

        {/* Privacy & Identity */}
        <Text style={[styles.sectionTitle, styles.sectionSpaced]}>
          Privacy & Identity
        </Text>

        <View style={styles.identityCard}>
          <Text style={styles.identityLabel}>Anonymous ID</Text>
          <Text style={styles.identityValue}>
            {identityRef ? `${identityRef}…` : 'Loading…'}
          </Text>
          <Text style={styles.identityHint}>
            No name, email, or device info is ever stored — this reference is
            derived locally and never leaves your device in this form.
          </Text>
        </View>

        <TouchableOpacity
          style={styles.dangerButton}
          onPress={handleResetIdentity}
          disabled={isResetting}
          accessibilityRole="button"
          accessibilityLabel="Reset anonymous identity"
        >
          <Text style={styles.dangerButtonText}>
            {isResetting ? 'Resetting…' : 'Reset Anonymous Identity'}
          </Text>
        </TouchableOpacity>

        {/* About */}
        <Text style={[styles.sectionTitle, styles.sectionSpaced]}>About</Text>
        <View style={styles.aboutCard}>
          <Text style={styles.aboutName}>Auri</Text>
          <Text style={styles.aboutVersion}>Version {appVersion}</Text>
          <Text style={styles.aboutTagline}>
            Speak freely. Be heard. Remain unknown.
          </Text>
        </View>
      </ScrollView>
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
    paddingVertical: spacing.md,
  },
  backButton: {
    width: 40,
    height: 40,
    justifyContent: 'center',
    alignItems: 'center',
  },
  backButtonText: {
    fontSize: typography.fontSize.xxl,
    color: colors.candleGlow,
  },
  headerTitle: {
    fontSize: typography.fontSize.lg,
    fontWeight: typography.fontWeight.semibold,
    color: colors.slate200,
  },
  scrollContent: {
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.xxxl,
  },
  sectionTitle: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.candleGlow,
    textTransform: 'uppercase',
    letterSpacing: 1,
    marginBottom: spacing.md,
  },
  sectionSpaced: {
    marginTop: spacing.xxl,
  },
  fieldLabel: {
    fontSize: typography.fontSize.sm,
    color: colors.slate300,
    marginBottom: spacing.sm,
  },
  fieldLabelSpaced: {
    marginTop: spacing.lg,
  },
  environmentRow: {
    flexDirection: 'row',
    gap: spacing.sm,
  },
  environmentOption: {
    flex: 1,
    paddingVertical: spacing.md,
    borderRadius: borderRadius.md,
    borderWidth: 1.5,
    borderColor: colors.slate700,
    backgroundColor: colors.slate800,
    alignItems: 'center',
  },
  environmentOptionSelected: {
    borderColor: colors.candleGlow,
    backgroundColor: `${colors.candleGlow}20`,
  },
  environmentOptionText: {
    fontSize: typography.fontSize.xs,
    color: colors.slate300,
    textAlign: 'center',
  },
  environmentOptionTextSelected: {
    color: colors.candleGlow,
    fontWeight: typography.fontWeight.semibold,
  },
  identityCard: {
    padding: spacing.lg,
    borderRadius: borderRadius.md,
    backgroundColor: colors.slate800,
    marginBottom: spacing.md,
  },
  identityLabel: {
    fontSize: typography.fontSize.xs,
    color: colors.slate400,
    marginBottom: spacing.xs,
  },
  identityValue: {
    fontSize: typography.fontSize.lg,
    fontWeight: typography.fontWeight.semibold,
    color: colors.slate200,
    fontFamily: 'monospace',
    marginBottom: spacing.sm,
  },
  identityHint: {
    fontSize: typography.fontSize.xs,
    color: colors.slate500,
    lineHeight: 18,
  },
  dangerButton: {
    paddingVertical: spacing.md,
    borderRadius: borderRadius.md,
    borderWidth: 1.5,
    borderColor: colors.deepCrimson,
    alignItems: 'center',
  },
  dangerButtonText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.deepCrimson,
  },
  aboutCard: {
    padding: spacing.lg,
    borderRadius: borderRadius.md,
    backgroundColor: colors.slate800,
    alignItems: 'center',
  },
  aboutName: {
    fontSize: typography.fontSize.xl,
    fontWeight: typography.fontWeight.bold,
    color: colors.candleGlow,
    letterSpacing: 2,
    textTransform: 'uppercase',
  },
  aboutVersion: {
    fontSize: typography.fontSize.xs,
    color: colors.slate500,
    marginTop: spacing.xs,
  },
  aboutTagline: {
    fontSize: typography.fontSize.sm,
    color: colors.slate400,
    fontStyle: 'italic',
    marginTop: spacing.md,
    textAlign: 'center',
  },
});
