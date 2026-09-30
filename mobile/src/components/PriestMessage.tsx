// Auri — Guide message
// One entry of the conversation: the user's question as a bubble, or the
// Guide's answer as a card. What the library says (points, quotes) is kept
// visually apart from the muted Reflection, and every answer ends with a
// one-line disclaimer. Quote labels are the server's; nothing here words a
// claim about scripture.

import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { colors } from '../theme/colors';
import { borderRadius, spacing, typography } from '../theme';
import type { ChatMessage } from '../hooks/usePriestConversation';
import type { CitationPresentation, DisplayBlock } from '../lib/priestPresentation';
import { PriestCrisisCard } from './PriestCrisisCard';

const MIN_TOUCH_TARGET = 44;
const CHIP_SIZE = 28;
const QUOTE_BAR_WIDTH = 3;

interface PriestMessageProps {
  message: ChatMessage;
  /** Called with the tapped source, to open the citation sheet. */
  onOpenCitation: (citation: CitationPresentation) => void;
}

type OpenCitation = (citationId: string) => void;

type PointsBlock = Extract<DisplayBlock, { type: 'points' }>;
type QuotesBlock = Extract<DisplayBlock, { type: 'quotes' }>;
type ExcerptsBlock = Extract<DisplayBlock, { type: 'excerpts' }>;
type ReflectionBlock = Extract<DisplayBlock, { type: 'reflection' }>;

function renderPoints(block: PointsBlock, open: OpenCitation): React.JSX.Element {
  return (
    <View style={styles.section}>
      {block.items.map((point, pointIndex) => (
        <View key={pointIndex} style={styles.point}>
          <Text style={styles.pointText}>{point.text}</Text>
          <View style={styles.chipRow}>
            {point.chips.map((chip, chipIndex) => (
              <TouchableOpacity
                key={`${chipIndex}-${chip.citationId}`}
                style={styles.chipTarget}
                onPress={() => open(chip.citationId)}
                accessibilityRole="button"
                accessibilityLabel={chip.accessibilityLabel}
              >
                <View style={styles.chip}>
                  <Text style={styles.chipText}>{chip.number}</Text>
                </View>
              </TouchableOpacity>
            ))}
          </View>
        </View>
      ))}
    </View>
  );
}

function renderQuotes(block: QuotesBlock): React.JSX.Element {
  return (
    <View style={styles.section}>
      {block.items.map((quote, quoteIndex) => (
        <View key={quoteIndex} style={styles.quote} accessible>
          <Text style={styles.quoteText}>{quote.text}</Text>
          <Text style={styles.quoteLabel}>{quote.label}</Text>
        </View>
      ))}
    </View>
  );
}

function renderReflection(block: ReflectionBlock): React.JSX.Element {
  return (
    <View style={styles.reflection}>
      <Text style={styles.reflectionHeading} accessibilityRole="header">
        {block.heading}
      </Text>
      <Text style={styles.reflectionText}>{block.text}</Text>
    </View>
  );
}

function renderExcerpts(block: ExcerptsBlock, open: OpenCitation): React.JSX.Element {
  return (
    <View style={styles.section}>
      {block.items.map((item, itemIndex) => (
        <TouchableOpacity
          key={`${itemIndex}-${item.citationId}`}
          style={styles.excerpt}
          onPress={() => open(item.citationId)}
          accessibilityRole="button"
          accessibilityLabel={item.accessibilityLabel}
        >
          <Text style={styles.excerptTitle}>{`${item.number}. ${item.title}`}</Text>
          <Text style={styles.excerptSnippet}>{item.snippet}</Text>
        </TouchableOpacity>
      ))}
    </View>
  );
}

function renderBlock(
  block: DisplayBlock,
  index: number,
  citations: CitationPresentation[],
  onOpenCitation: (citation: CitationPresentation) => void,
): React.JSX.Element {
  const open: OpenCitation = (citationId) => {
    const found = citations.find((c) => c.id === citationId);
    if (found) onOpenCitation(found);
  };
  switch (block.type) {
    case 'points':
      return <React.Fragment key={index}>{renderPoints(block, open)}</React.Fragment>;
    case 'quotes':
      return <React.Fragment key={index}>{renderQuotes(block)}</React.Fragment>;
    case 'reflection':
      return <React.Fragment key={index}>{renderReflection(block)}</React.Fragment>;
    case 'excerpts':
      return <React.Fragment key={index}>{renderExcerpts(block, open)}</React.Fragment>;
    case 'crisis':
      return <PriestCrisisCard key={index} block={block} />;
    case 'notice':
      return (
        <Text key={index} style={styles.notice}>
          {block.text}
        </Text>
      );
    case 'invitation':
      return (
        <Text key={index} style={styles.invitation}>
          {block.text}
        </Text>
      );
    default:
      return (
        <Text key={index} style={styles.disclaimer}>
          {block.text}
        </Text>
      );
  }
}

/** Renders a user question bubble, or a Guide answer card built from display blocks. */
export function PriestMessage({ message, onOpenCitation }: PriestMessageProps): React.JSX.Element {
  if (message.role === 'user') {
    return (
      <View style={styles.userBubble} accessible accessibilityLabel={`You asked: ${message.text}`}>
        <Text style={styles.userText}>{message.text}</Text>
      </View>
    );
  }
  const { blocks, citations } = message.presentation;
  const isCrisis = blocks.length === 1 && blocks[0]?.type === 'crisis';
  return (
    <View style={isCrisis ? styles.crisisWrapper : styles.answerCard}>
      {blocks.map((block, index) => renderBlock(block, index, citations, onOpenCitation))}
    </View>
  );
}

const styles = StyleSheet.create({
  userBubble: {
    alignSelf: 'flex-end',
    maxWidth: '85%',
    marginVertical: spacing.sm,
    padding: spacing.md,
    borderRadius: borderRadius.lg,
    backgroundColor: `${colors.candleGlow}25`,
  },
  userText: {
    fontSize: typography.fontSize.md,
    color: colors.slate200,
  },
  answerCard: {
    alignSelf: 'stretch',
    marginVertical: spacing.sm,
    padding: spacing.lg,
    borderRadius: borderRadius.lg,
    backgroundColor: colors.slate800,
  },
  crisisWrapper: {
    alignSelf: 'stretch',
    marginVertical: spacing.sm,
  },
  section: {
    marginBottom: spacing.md,
  },
  point: {
    marginBottom: spacing.sm,
  },
  pointText: {
    fontSize: typography.fontSize.md,
    lineHeight: 24,
    color: colors.slate200,
  },
  chipRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
  },
  chipTarget: {
    minWidth: MIN_TOUCH_TARGET,
    minHeight: MIN_TOUCH_TARGET,
    alignItems: 'center',
    justifyContent: 'center',
  },
  chip: {
    width: CHIP_SIZE,
    height: CHIP_SIZE,
    borderRadius: CHIP_SIZE / 2,
    borderWidth: 1.5,
    borderColor: colors.candleGlow,
    alignItems: 'center',
    justifyContent: 'center',
  },
  chipText: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.semibold,
    color: colors.candleGlow,
  },
  quote: {
    marginBottom: spacing.sm,
    paddingLeft: spacing.md,
    borderLeftWidth: QUOTE_BAR_WIDTH,
    borderLeftColor: colors.candleGlow,
  },
  quoteText: {
    fontSize: typography.fontSize.md,
    fontStyle: 'italic',
    lineHeight: 24,
    color: colors.slate300,
  },
  quoteLabel: {
    marginTop: spacing.xs,
    fontSize: typography.fontSize.xs,
    color: colors.slate400,
  },
  reflection: {
    marginBottom: spacing.md,
    paddingTop: spacing.md,
    borderTopWidth: 1,
    borderTopColor: colors.slate700,
  },
  reflectionHeading: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.semibold,
    color: colors.slate500,
    textTransform: 'uppercase',
    letterSpacing: 1,
  },
  reflectionText: {
    marginTop: spacing.xs,
    fontSize: typography.fontSize.sm,
    lineHeight: 22,
    color: colors.slate400,
  },
  excerpt: {
    minHeight: MIN_TOUCH_TARGET,
    marginBottom: spacing.sm,
    padding: spacing.md,
    borderRadius: borderRadius.md,
    backgroundColor: colors.slate900,
  },
  excerptTitle: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.slate200,
  },
  excerptSnippet: {
    marginTop: spacing.xs,
    fontSize: typography.fontSize.sm,
    color: colors.slate400,
  },
  notice: {
    marginBottom: spacing.sm,
    fontSize: typography.fontSize.md,
    lineHeight: 24,
    color: colors.slate200,
  },
  invitation: {
    marginBottom: spacing.sm,
    fontSize: typography.fontSize.sm,
    color: colors.slate400,
  },
  disclaimer: {
    fontSize: typography.fontSize.xs,
    color: colors.slate500,
  },
});
