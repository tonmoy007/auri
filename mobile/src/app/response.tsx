// Auri — Response screen
// Shows a deliberate, compassionate AI response after a confession is
// submitted, modeled on how a confessor listens: acknowledge, then reply.

import React, { useCallback, useMemo } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  SafeAreaView,
  ScrollView,
  useWindowDimensions,
} from 'react-native';
import { router, Stack, useLocalSearchParams } from 'expo-router';
import { colors } from '../theme/colors';
import { typography, spacing } from '../theme';
import { ConfessionBooth } from '../components/ConfessionBooth';
import { ThreeCanvas } from '../components/ThreeCanvas';
import { useHaptics } from '../hooks/useHaptics';
import {
  SUGGESTIONS_HEADING,
  presentCounsel,
  type CounselPresentation,
} from '../lib/counselPresentation';

/** The reply card may take at most this share of the screen height, then it scrolls. */
const MAX_CARD_HEIGHT_RATIO = 0.45;

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

/** The reply as the server sent it: prose, then optional suggestions, then the closing. */
function CounselCard({ counsel }: { counsel: CounselPresentation }): React.JSX.Element {
  if (counsel.kind === 'plain') {
    return <Text style={styles.responseText}>{counsel.text}</Text>;
  }
  return (
    <View>
      {counsel.prose.map((paragraph) => (
        <Text key={paragraph} style={[styles.responseText, styles.paragraph]}>
          {paragraph}
        </Text>
      ))}
      {counsel.suggestions.length > 0 ? (
        <View style={styles.suggestions}>
          <Text style={styles.suggestionsHeading} accessibilityRole="header">
            {SUGGESTIONS_HEADING}
          </Text>
          {counsel.suggestions.map((suggestion) => (
            <View key={suggestion} style={styles.suggestionRow}>
              <View style={styles.suggestionDot} />
              <Text style={styles.suggestionText}>{suggestion}</Text>
            </View>
          ))}
        </View>
      ) : null}
      <Text style={[styles.responseText, styles.closing]}>{counsel.closing}</Text>
    </View>
  );
}

/**
 * Response screen — the confessor's booth visit doesn't end at "submitted".
 * A short, compassionate AI reply stands between submission and whatever
 * comes next (fully anonymous exit, or picking a department to forward to).
 */
export default function ResponseScreen(): React.JSX.Element {
  const rawParams = useLocalSearchParams();
  const id = readStringParam(rawParams, 'id') ?? '';
  const counselorResponseParam = readStringParam(rawParams, 'counselorResponse');
  const counselorReplyParam = readStringParam(rawParams, 'counselorReply');
  const anonymityParam = readStringParam(rawParams, 'anonymityEnabled');
  const anonymityEnabled = anonymityParam !== '0';
  const haptics = useHaptics();

  const { height } = useWindowDimensions();
  const counsel = useMemo(
    () => presentCounsel(counselorReplyParam, counselorResponseParam),
    [counselorReplyParam, counselorResponseParam],
  );

  const handleContinue = useCallback(() => {
    haptics.selectionChanged();
    if (anonymityEnabled) {
      // Fully blind — nothing more to choose, exit the booth entirely.
      router.dismissAll();
      router.replace('/');
      return;
    }
    // "Someone in your team" — still needs a department target.
    router.replace({
      pathname: '/forward/[id]',
      params: { id },
    });
  }, [anonymityEnabled, id, haptics]);

  return (
    <SafeAreaView style={styles.container}>
      <Stack.Screen options={{ title: 'You Are Heard', headerShown: false }} />

      <View style={styles.threeContainer}>
        <ThreeCanvas>
          <ConfessionBooth environment="classic" />
        </ThreeCanvas>
      </View>

      <View style={styles.overlay}>
        <Text style={styles.eyebrow}>Your confession has been received</Text>

        <ScrollView style={[styles.card, { maxHeight: height * MAX_CARD_HEIGHT_RATIO }]}>
          <CounselCard counsel={counsel} />
        </ScrollView>

        <TouchableOpacity
          style={styles.continueButton}
          onPress={handleContinue}
          activeOpacity={0.8}
          accessibilityRole="button"
          accessibilityLabel="Continue"
        >
          <Text style={styles.continueButtonText}>
            {anonymityEnabled ? 'Done' : 'Choose Department'}
          </Text>
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
  eyebrow: {
    fontSize: typography.fontSize.xs,
    color: colors.slate400,
    textAlign: 'center',
    letterSpacing: 1,
    textTransform: 'uppercase',
    marginBottom: spacing.md,
  },
  card: {
    backgroundColor: colors.slate800,
    borderRadius: 12,
    padding: spacing.lg,
    borderWidth: 1,
    borderColor: colors.slate700,
  },
  responseText: {
    fontSize: typography.fontSize.md,
    color: colors.slate200,
    lineHeight: 24,
    fontStyle: 'italic',
    textAlign: 'center',
  },
  paragraph: {
    marginBottom: spacing.md,
  },
  suggestions: {
    marginBottom: spacing.md,
    padding: spacing.md,
    borderRadius: 8,
    backgroundColor: colors.slate700,
  },
  suggestionsHeading: {
    fontSize: typography.fontSize.xs,
    color: colors.slate400,
    letterSpacing: 1,
    textTransform: 'uppercase',
    marginBottom: spacing.sm,
  },
  suggestionRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    marginTop: spacing.xs,
  },
  suggestionDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    marginTop: 9,
    marginRight: spacing.sm,
    backgroundColor: colors.candleGlow,
  },
  suggestionText: {
    flex: 1,
    fontSize: typography.fontSize.md,
    color: colors.slate200,
    lineHeight: 24,
  },
  closing: {
    marginTop: spacing.xs,
  },
  continueButton: {
    marginTop: spacing.xl,
    paddingVertical: spacing.md,
    borderRadius: 12,
    backgroundColor: colors.candleGlow,
    alignItems: 'center',
    justifyContent: 'center',
  },
  continueButtonText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.boothDark,
    letterSpacing: 1,
    textTransform: 'uppercase',
  },
});
