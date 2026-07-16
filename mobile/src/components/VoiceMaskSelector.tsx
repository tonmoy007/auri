// Auri — Voice mask selector component
// 5 voice mask cards (Warm, Robotic, Ethereal, Deep, Random) with visual feedback

import React from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
} from 'react-native';
import { colors } from '../theme/colors';
import { typography, spacing } from '../theme';
import type { VoiceMask } from '../types';

interface VoiceMaskSelectorProps {
  /** Currently selected mask */
  selected: VoiceMask;
  /** Called when a mask is selected */
  onSelect: (mask: VoiceMask) => void;
  /** Whether selection is disabled (e.g., during recording) */
  disabled?: boolean;
}

interface MaskOption {
  id: VoiceMask;
  label: string;
  description: string;
  color: string;
  accentColor: string;
}

/**
 * Voice mask options with their visual identities.
 * Each mask has a distinct color scheme for immediate recognition.
 */
const maskOptions: MaskOption[] = [
  {
    id: 'warm',
    label: 'Warm',
    description: 'Soft and comforting',
    color: colors.warmOrange,
    accentColor: '#f97316',
  },
  {
    id: 'robotic',
    label: 'Robotic',
    description: 'Digital and precise',
    color: colors.roboticCyan,
    accentColor: '#06b6d4',
  },
  {
    id: 'ethereal',
    label: 'Ethereal',
    description: 'Airy and mysterious',
    color: colors.etherealPurple,
    accentColor: '#a855f7',
  },
  {
    id: 'deep',
    label: 'Deep',
    description: 'Rich and resonant',
    color: colors.deepCrimson,
    accentColor: '#e11d48',
  },
  {
    id: 'random',
    label: 'Random',
    description: 'Surprise each time',
    color: colors.randomGold,
    accentColor: '#eab308',
  },
];

/**
 * Horizontal scrollable voice mask selector.
 * Each card shows a colored accent border and glows when selected.
 */
export function VoiceMaskSelector({
  selected,
  onSelect,
  disabled = false,
}: VoiceMaskSelectorProps): React.JSX.Element {
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      contentContainerStyle={styles.scrollContent}
      style={styles.container}
    >
      {maskOptions.map((mask) => {
        const isSelected = selected === mask.id;

        return (
          <TouchableOpacity
            key={mask.id}
            style={[
              styles.card,
              {
                borderColor: isSelected
                  ? mask.accentColor
                  : 'transparent',
                backgroundColor: isSelected
                  ? `${mask.color}20`
                  : colors.slate800,
              },
              isSelected && styles.selectedCard,
            ]}
            onPress={() => onSelect(mask.id)}
            disabled={disabled}
            activeOpacity={0.7}
            accessibilityRole="radio"
            accessibilityState={{ selected: isSelected, disabled }}
            accessibilityLabel={`${mask.label} voice mask: ${mask.description}`}
          >
            {/* Color indicator dot */}
            <View
              style={[
                styles.indicator,
                { backgroundColor: mask.accentColor },
              ]}
            />

            {/* Mask label */}
            <Text
              style={[
                styles.label,
                isSelected && { color: mask.accentColor },
              ]}
            >
              {mask.label}
            </Text>

            {/* Description */}
            <Text style={styles.description}>{mask.description}</Text>

            {/* Selection glow */}
            {isSelected && (
              <View
                style={[
                  styles.glow,
                  { backgroundColor: mask.accentColor },
                ]}
              />
            )}
          </TouchableOpacity>
        );
      })}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flexGrow: 0,
  },
  scrollContent: {
    paddingHorizontal: spacing.md,
    gap: spacing.sm,
  },
  card: {
    width: 110,
    padding: spacing.md,
    borderRadius: 16,
    borderWidth: 1.5,
    alignItems: 'center',
    position: 'relative',
    overflow: 'hidden',
  },
  selectedCard: {
    shadowColor: '#fff',
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.1,
    shadowRadius: 10,
    elevation: 4,
  },
  indicator: {
    width: 8,
    height: 8,
    borderRadius: 4,
    marginBottom: spacing.xs,
  },
  label: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.slate200,
    marginBottom: 2,
  },
  description: {
    fontSize: typography.fontSize.xs,
    color: colors.slate400,
    textAlign: 'center',
  },
  glow: {
    position: 'absolute',
    top: -20,
    right: -20,
    width: 40,
    height: 40,
    borderRadius: 20,
    opacity: 0.15,
  },
});
