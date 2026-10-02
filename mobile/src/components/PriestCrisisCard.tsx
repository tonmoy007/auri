// Auri — Guide crisis card
// A distinct, prominent card for a crisis reply. Its words are fixed in
// priestPresentation, with the server's own fixed template text shown beside
// them. Its buttons only open the phone's own dialer (a `tel:` link built from
// a strictly validated number); nothing goes over the network from here.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Linking, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { colors } from '../theme/colors';
import { borderRadius, spacing, typography } from '../theme';
import { useHaptics } from '../hooks/useHaptics';
import type { CrisisBlock } from '../lib/priestPresentation';

const MIN_TOUCH_TARGET = 48;
const BORDER_WIDTH = 2;

interface PriestCrisisCardProps {
  block: CrisisBlock;
  /**
   * `reply` is a crisis answer: it is announced and buzzes once when it appears.
   * `static` is the always-available help block: no alert, no haptic.
   */
  variant?: 'reply' | 'static';
}

/**
 * Crisis card: fixed heading and text, the contacts, and a call button for each
 * contact that has a safe number. A crisis reply fires a warning haptic once per
 * mounted card, however often the screen around it re-renders.
 */
export function PriestCrisisCard({
  block,
  variant = 'reply',
}: PriestCrisisCardProps): React.JSX.Element {
  const { warning } = useHaptics();
  const [dialFailure, setDialFailure] = useState<string | null>(null);
  const hasBuzzedRef = useRef(false);
  const isReply = variant === 'reply';

  useEffect(() => {
    if (!isReply || hasBuzzedRef.current) return;
    hasBuzzedRef.current = true;
    warning();
  }, [isReply, warning]);

  const handleCall = useCallback((telUrl: string, detail: string) => {
    setDialFailure(null);
    Linking.openURL(telUrl).catch(() => setDialFailure(detail));
  }, []);

  return (
    <View
      style={styles.card}
      accessibilityRole={isReply ? 'alert' : undefined}
      accessibilityLiveRegion={isReply ? 'assertive' : undefined}
    >
      <Text style={styles.heading} accessibilityRole="header">
        {block.heading}
      </Text>
      <Text style={styles.body}>{block.body}</Text>
      {block.serverText ? (
        <Text style={styles.serverText} selectable>
          {block.serverText}
        </Text>
      ) : null}
      {block.contacts.map((contact, index) => (
        <View key={`${index}-${contact.label}-${contact.detail}`} style={styles.contactRow}>
          <View style={styles.contactText}>
            <Text style={styles.contactLabel}>{contact.label}</Text>
            <Text style={styles.contactDetail} selectable>
              {contact.detail}
            </Text>
          </View>
          {contact.telUrl ? (
            <TouchableOpacity
              style={styles.callButton}
              onPress={() => handleCall(contact.telUrl ?? '', contact.detail)}
              accessibilityRole="button"
              accessibilityLabel={contact.accessibilityLabel}
            >
              <Text style={styles.callButtonText}>Call</Text>
            </TouchableOpacity>
          ) : null}
        </View>
      ))}
      {dialFailure ? (
        <Text style={styles.dialFailure}>
          {`Couldn't open the dialer. You can dial ${dialFailure} yourself.`}
        </Text>
      ) : null}
      <Text style={styles.emergency}>{block.emergencyLine}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    padding: spacing.lg,
    borderRadius: borderRadius.lg,
    borderWidth: BORDER_WIDTH,
    borderColor: colors.rose400,
    backgroundColor: colors.slate800,
  },
  heading: {
    fontSize: typography.fontSize.lg,
    fontWeight: typography.fontWeight.bold,
    color: colors.white,
  },
  body: {
    marginTop: spacing.sm,
    fontSize: typography.fontSize.md,
    lineHeight: 24,
    color: colors.slate200,
  },
  serverText: {
    marginTop: spacing.sm,
    fontSize: typography.fontSize.md,
    lineHeight: 24,
    color: colors.slate300,
  },
  contactRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: spacing.md,
    gap: spacing.md,
  },
  contactText: {
    flex: 1,
  },
  contactLabel: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.slate200,
  },
  contactDetail: {
    marginTop: spacing.xs,
    fontSize: typography.fontSize.md,
    color: colors.white,
  },
  callButton: {
    minWidth: MIN_TOUCH_TARGET,
    minHeight: MIN_TOUCH_TARGET,
    paddingHorizontal: spacing.lg,
    borderRadius: borderRadius.full,
    backgroundColor: colors.rose500,
    alignItems: 'center',
    justifyContent: 'center',
  },
  callButtonText: {
    fontSize: typography.fontSize.md,
    fontWeight: typography.fontWeight.bold,
    color: colors.white,
  },
  dialFailure: {
    marginTop: spacing.sm,
    fontSize: typography.fontSize.sm,
    color: colors.rose400,
  },
  emergency: {
    marginTop: spacing.md,
    fontSize: typography.fontSize.sm,
    color: colors.slate300,
  },
});
