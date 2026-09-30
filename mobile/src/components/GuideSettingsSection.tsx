// Auri — Settings: Guide section
// The toggle that shows or hides the Guide on the home screen, and an optional
// tradition to limit answers to. Both are stored on this device only; the
// tradition leaves it solely as the per-request `tradition` field.

import React, { useCallback } from 'react';
import { StyleSheet, Switch, Text, TouchableOpacity, View } from 'react-native';
import { colors } from '../theme/colors';
import { borderRadius, spacing, typography } from '../theme';
import { useHaptics } from '../hooks/useHaptics';
import { PRIVACY_LINE } from '../lib/priestPresentation';
import { TRADITION_IDS, TRADITION_LABELS, type TraditionId } from '../types/priest';

const MIN_TOUCH_TARGET = 44;

interface GuideSettingsSectionProps {
  showGuideMode: boolean;
  guideTradition: TraditionId | null;
  onChangeShowGuideMode: (show: boolean) => void;
  onChangeGuideTradition: (tradition: TraditionId | null) => void;
}

/** Settings block for the Guide: show/hide toggle, optional tradition, privacy note. */
export function GuideSettingsSection({
  showGuideMode,
  guideTradition,
  onChangeShowGuideMode,
  onChangeGuideTradition,
}: GuideSettingsSectionProps): React.JSX.Element {
  const haptics = useHaptics();

  const handleTradition = useCallback(
    (tradition: TraditionId | null) => {
      haptics.selectionChanged();
      onChangeGuideTradition(tradition);
    },
    [haptics, onChangeGuideTradition],
  );

  const options: { id: TraditionId | null; label: string }[] = [
    { id: null, label: 'All traditions' },
    ...TRADITION_IDS.map((id) => ({ id, label: TRADITION_LABELS[id] })),
  ];

  return (
    <View>
      <Text style={styles.sectionTitle}>Guide</Text>
      <View style={styles.toggleRow}>
        <Text style={styles.toggleLabel}>Show the Guide on the home screen</Text>
        <Switch
          value={showGuideMode}
          onValueChange={onChangeShowGuideMode}
          trackColor={{ false: colors.slate700, true: colors.candleGlow }}
          accessibilityRole="switch"
          accessibilityLabel="Show the Guide on the home screen"
          accessibilityState={{ checked: showGuideMode }}
        />
      </View>

      <Text style={styles.fieldLabel}>Limit answers to one tradition (optional)</Text>
      <View style={styles.optionWrap}>
        {options.map((option) => {
          const isSelected = guideTradition === option.id;
          return (
            <TouchableOpacity
              key={option.id ?? 'all'}
              style={[styles.option, isSelected && styles.optionSelected]}
              onPress={() => handleTradition(option.id)}
              accessibilityRole="radio"
              accessibilityState={{ selected: isSelected }}
              accessibilityLabel={option.label}
            >
              <Text style={[styles.optionText, isSelected && styles.optionTextSelected]}>
                {option.label}
              </Text>
            </TouchableOpacity>
          );
        })}
      </View>
      <Text style={styles.hint}>
        {`${PRIVACY_LINE} The tradition you pick stays on this device.`}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  sectionTitle: {
    marginBottom: spacing.md,
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.candleGlow,
    textTransform: 'uppercase',
    letterSpacing: 1,
  },
  toggleRow: {
    minHeight: MIN_TOUCH_TARGET,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.md,
    marginBottom: spacing.lg,
  },
  toggleLabel: {
    flex: 1,
    fontSize: typography.fontSize.md,
    color: colors.slate200,
  },
  fieldLabel: {
    marginBottom: spacing.sm,
    fontSize: typography.fontSize.sm,
    color: colors.slate300,
  },
  optionWrap: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
  },
  option: {
    minHeight: MIN_TOUCH_TARGET,
    paddingHorizontal: spacing.md,
    justifyContent: 'center',
    borderRadius: borderRadius.md,
    borderWidth: 1.5,
    borderColor: colors.slate700,
    backgroundColor: colors.slate800,
  },
  optionSelected: {
    borderColor: colors.candleGlow,
    backgroundColor: `${colors.candleGlow}20`,
  },
  optionText: {
    fontSize: typography.fontSize.sm,
    color: colors.slate300,
  },
  optionTextSelected: {
    fontWeight: typography.fontWeight.semibold,
    color: colors.candleGlow,
  },
  hint: {
    marginTop: spacing.md,
    fontSize: typography.fontSize.xs,
    lineHeight: 18,
    color: colors.slate500,
  },
});
