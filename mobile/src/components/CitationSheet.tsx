// Auri — Guide citation sheet
// A modal showing the note behind a citation chip: title, heading path,
// tradition and the snippet. Read-only and link-free on purpose, like
// HrReplyCard: a tappable link out of the app is not something the Guide offers.

import React from 'react';
import {
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { colors } from '../theme/colors';
import { borderRadius, spacing, typography } from '../theme';
import { useReducedMotion } from '../hooks/useReducedMotion';
import { CITATION_SHEET_HEADING, type CitationPresentation } from '../lib/priestPresentation';

const MIN_TOUCH_TARGET = 44;
const SHEET_MAX_HEIGHT = '75%';
const BACKDROP = 'rgba(2, 6, 23, 0.7)';

interface CitationSheetProps {
  /** The citation to show; `null` keeps the sheet closed. */
  citation: CitationPresentation | null;
  onClose: () => void;
}

/** Bottom sheet for one citation, labelled as coming from Auri's study library. */
export function CitationSheet({ citation, onClose }: CitationSheetProps): React.JSX.Element {
  const reduceMotion = useReducedMotion();

  return (
    <Modal
      visible={citation !== null}
      transparent
      animationType={reduceMotion ? 'none' : 'slide'}
      onRequestClose={onClose}
    >
      <View style={styles.backdrop}>
        <Pressable
          style={styles.dismissArea}
          onPress={onClose}
          accessibilityRole="button"
          accessibilityLabel="Close source"
        />
        {citation ? (
          <View style={styles.sheet} accessibilityViewIsModal>
            <View style={styles.sheetHeader}>
              <Text style={styles.libraryLabel}>{CITATION_SHEET_HEADING}</Text>
              <TouchableOpacity
                style={styles.closeButton}
                onPress={onClose}
                accessibilityRole="button"
                accessibilityLabel="Close source"
              >
                <Text style={styles.closeButtonText}>Close</Text>
              </TouchableOpacity>
            </View>
            <ScrollView>
              <Text style={styles.title} accessibilityRole="header">
                {citation.title}
              </Text>
              {citation.headingPath ? (
                <Text style={styles.meta}>{citation.headingPath}</Text>
              ) : null}
              {citation.traditions ? (
                <Text style={styles.meta}>{`Tradition: ${citation.traditions}`}</Text>
              ) : null}
              <Text style={styles.snippet}>{citation.snippet}</Text>
            </ScrollView>
          </View>
        ) : null}
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    justifyContent: 'flex-end',
    backgroundColor: BACKDROP,
  },
  dismissArea: {
    flex: 1,
  },
  sheet: {
    maxHeight: SHEET_MAX_HEIGHT,
    padding: spacing.lg,
    borderTopLeftRadius: borderRadius.xl,
    borderTopRightRadius: borderRadius.xl,
    backgroundColor: colors.slate800,
  },
  sheetHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: spacing.md,
  },
  libraryLabel: {
    flex: 1,
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.semibold,
    color: colors.candleGlow,
    textTransform: 'uppercase',
    letterSpacing: 1,
  },
  closeButton: {
    minWidth: MIN_TOUCH_TARGET,
    minHeight: MIN_TOUCH_TARGET,
    alignItems: 'center',
    justifyContent: 'center',
  },
  closeButtonText: {
    fontSize: typography.fontSize.md,
    color: colors.candleGlow,
  },
  title: {
    fontSize: typography.fontSize.lg,
    fontWeight: typography.fontWeight.semibold,
    color: colors.slate200,
  },
  meta: {
    marginTop: spacing.xs,
    fontSize: typography.fontSize.sm,
    color: colors.slate400,
  },
  snippet: {
    marginTop: spacing.md,
    fontSize: typography.fontSize.md,
    lineHeight: 24,
    color: colors.slate300,
  },
});
