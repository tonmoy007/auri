// Auri — HR reply card
// Read-only block shown under a confession in the history screen when the
// organisation has replied. Deliberately inert: the reply is staff-written text,
// so it is rendered as plain text with no links, selection or tap handling.
// A tappable link could reveal the confessor's IP and device to a server HR controls.

import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { colors } from '../theme/colors';
import { typography, spacing, borderRadius } from '../theme';
import type { HrReplyPresentation } from '../lib/historyPresentation';

const ACCENT_BORDER_WIDTH = 3;

interface HrReplyCardProps {
  reply: HrReplyPresentation;
}

/**
 * Shows an HR reply with a static "Reply from HR" heading, when it was sent
 * (and edited), and the full reply text. The whole card is one accessibility
 * element so screen readers announce it in a single pass.
 */
export function HrReplyCard({ reply }: HrReplyCardProps): React.JSX.Element {
  return (
    <View style={styles.container} accessible accessibilityLabel={reply.accessibilityLabel}>
      <Text style={styles.heading}>{reply.heading}</Text>
      <Text style={styles.meta}>{reply.meta}</Text>
      <Text style={styles.body}>{reply.body}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginTop: spacing.md,
    padding: spacing.md,
    borderRadius: borderRadius.md,
    backgroundColor: colors.slate900,
    borderLeftWidth: ACCENT_BORDER_WIDTH,
    borderLeftColor: colors.candleGlow,
  },
  heading: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.slate200,
  },
  meta: {
    marginTop: spacing.xs,
    fontSize: typography.fontSize.xs,
    color: colors.slate500,
  },
  body: {
    marginTop: spacing.sm,
    fontSize: typography.fontSize.sm,
    color: colors.slate300,
  },
});
