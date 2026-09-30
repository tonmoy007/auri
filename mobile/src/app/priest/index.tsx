// Auri — Guide conversation
// Ask the Guide a question and read what the study library says, with sources.
// The conversation lives in memory only: leaving the screen or tapping Clear
// discards it. A dark slate screen with no 3D canvas, to keep it light.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  SafeAreaView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { router, Stack } from 'expo-router';
import { colors } from '../../theme/colors';
import { borderRadius, spacing, typography } from '../../theme';
import { CitationSheet } from '../../components/CitationSheet';
import { PriestComposer } from '../../components/PriestComposer';
import { PriestMessage } from '../../components/PriestMessage';
import { usePriestConversation, type ChatMessage } from '../../hooks/usePriestConversation';
import { usePriestStatus } from '../../hooks/usePriestStatus';
import { PriestCrisisCard } from '../../components/PriestCrisisCard';
import { useReducedMotion } from '../../hooks/useReducedMotion';
import { useRetryCountdown } from '../../hooks/useRetryCountdown';
import { hasAcknowledgedPriestIntro, useSettings } from '../../hooks/useSettings';
import {
  canSubmitQuestion,
  resolveTradition,
  traditionUnavailableNotice,
} from '../../lib/priestInput';
import {
  EMPTY_CONVERSATION_TEXT,
  PRIVACY_LINE,
  pendingStageText,
  presentError,
  presentHelpNow,
  retryLabel,
  type CitationPresentation,
  type ErrorPresentation,
} from '../../lib/priestPresentation';
import type { PriestErrorInfo, PriestStatus } from '../../types/priest';

const MIN_TOUCH_TARGET = 44;
type Gate = 'checking' | 'needs_intro' | 'ok' | 'failed';

function renderMessage(
  item: ChatMessage,
  onOpenCitation: (citation: CitationPresentation) => void,
): React.JSX.Element {
  return <PriestMessage message={item} onOpenCitation={onOpenCitation} />;
}

function statusFailure(
  statusError: PriestErrorInfo | null,
  status: PriestStatus | null,
): ErrorPresentation | null {
  if (statusError) return presentError(statusError);
  if (status && !status.enabled) return presentError({ code: 'disabled', retryAfterSeconds: null });
  return null;
}

function renderActionButton(
  label: string,
  onPress: () => void,
  disabled = false,
): React.JSX.Element {
  return (
    <TouchableOpacity
      style={styles.errorAction}
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled }}
    >
      <Text style={[styles.errorActionText, disabled && styles.errorActionTextDisabled]}>
        {label}
      </Text>
    </TouchableOpacity>
  );
}

interface RetryControl {
  onRetry: () => void;
  label: string;
  disabled: boolean;
}

function renderErrorBanner(error: ErrorPresentation, retry: RetryControl): React.JSX.Element {
  return (
    <View style={styles.errorBanner} accessibilityRole="alert" accessibilityLiveRegion="polite">
      <Text style={styles.errorText}>{error.message}</Text>
      <Text style={styles.helpLine}>{error.helpLine}</Text>
      {error.offersBack ? renderActionButton('Go back', () => router.back()) : null}
      {error.retryable ? renderActionButton(retry.label, retry.onRetry, retry.disabled) : null}
    </View>
  );
}

function renderStatusFallback(
  failure: ErrorPresentation | null,
  isWaiting: boolean,
  onRetry: () => void,
): React.JSX.Element {
  return (
    <View style={styles.center}>
      {isWaiting ? <ActivityIndicator color={colors.candleGlow} accessibilityLabel="Loading" /> : null}
      {failure ? (
        <View accessibilityRole="alert">
          <Text style={styles.failureText}>{failure.message}</Text>
          <Text style={[styles.helpLine, styles.failureHelpLine]}>{failure.helpLine}</Text>
          {failure.retryable
            ? renderActionButton('Try again', onRetry)
            : renderActionButton('Go back', () => router.back())}
        </View>
      ) : null}
    </View>
  );
}

/** The Guide screen: status and intro gate first, then the conversation. */
export default function PriestScreen(): React.JSX.Element {
  const { status, error: statusError, isLoading, reload } = usePriestStatus();
  const { guideTradition } = useSettings();
  const tradition = resolveTradition(guideTradition, status?.traditions ?? []);
  const maxChars = status?.max_question_chars ?? 0;
  const conversation = usePriestConversation(tradition, maxChars);
  const reduceMotion = useReducedMotion();
  const listRef = useRef<FlatList<ChatMessage>>(null);
  const [gate, setGate] = useState<Gate>('checking');
  const [gateAttempt, setGateAttempt] = useState(0);
  const [openCitation, setOpenCitation] = useState<CitationPresentation | null>(null);
  const [isVoiceBusy, setIsVoiceBusy] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const helpBlock = useMemo(() => presentHelpNow(status?.crisis_contacts), [status]);
  const retryWait = useRetryCountdown(
    conversation.errorAt,
    conversation.error?.retryAfterSeconds ?? null,
  );

  // A new disclaimer version, or a first visit that skipped the home screen's
  // check (a deep link), sends the user through the intro first.
  useEffect(() => {
    if (!status || !status.enabled) return undefined;
    let cancelled = false;
    hasAcknowledgedPriestIntro(status.disclaimer_version)
      .then((acknowledged) => {
        if (cancelled) return;
        setGate(acknowledged ? 'ok' : 'needs_intro');
        if (!acknowledged) router.replace('/priest/intro');
      })
      .catch(() => {
        // A failed storage read must not leave the spinner running for ever.
        if (!cancelled) setGate('failed');
      });
    return () => {
      cancelled = true;
    };
  }, [status, gateAttempt]);

  const handleRetry = useCallback(() => {
    // The same rules as the Send button: not while recording, not an unsendable
    // question, not before the wait the server asked for is over.
    const canRetry =
      !isVoiceBusy && retryWait === 0 && canSubmitQuestion(conversation.draft, maxChars);
    if (canRetry) conversation.send();
  }, [isVoiceBusy, retryWait, conversation, maxChars]);

  const scrollToEnd = useCallback(() => {
    listRef.current?.scrollToEnd({ animated: !reduceMotion });
  }, [reduceMotion]);

  const failure =
    gate === 'failed' && !statusError
      ? presentError({ code: 'unexpected', retryAfterSeconds: null })
      : statusFailure(statusError, status);
  const isReady = status !== null && status.enabled && gate === 'ok';
  const hasContent = conversation.messages.length > 0 || conversation.draft.length > 0;
  const canRetryNow =
    !isVoiceBusy && retryWait === 0 && canSubmitQuestion(conversation.draft, maxChars);
  const traditionNotice = traditionUnavailableNotice(guideTradition, status?.traditions ?? []);

  return (
    <SafeAreaView style={styles.container}>
      <Stack.Screen options={{ headerShown: false }} />
      <View style={styles.header}>
        <TouchableOpacity
          style={styles.headerButton}
          onPress={() => router.back()}
          accessibilityRole="button"
          accessibilityLabel="Go back"
        >
          <Text style={styles.backText}>‹</Text>
        </TouchableOpacity>
        <Text style={styles.headerTitle} accessibilityRole="header">
          {status?.persona_name ?? 'Guide'}
        </Text>
        <TouchableOpacity
          style={styles.headerButton}
          onPress={conversation.clear}
          disabled={!isReady || conversation.isPending || !hasContent}
          accessibilityRole="button"
          accessibilityLabel="Clear conversation"
          accessibilityState={{ disabled: !isReady || conversation.isPending || !hasContent }}
        >
          <Text style={[styles.clearText, !hasContent && styles.clearTextDisabled]}>Clear</Text>
        </TouchableOpacity>
      </View>

      <View style={styles.helpRow}>
        <TouchableOpacity
          style={styles.helpButton}
          onPress={() => setShowHelp((shown) => !shown)}
          accessibilityRole="button"
          accessibilityLabel={showHelp ? 'Hide help' : 'Need help now?'}
          accessibilityState={{ expanded: showHelp }}
        >
          <Text style={styles.helpButtonText}>{showHelp ? 'Hide help' : 'Need help now?'}</Text>
        </TouchableOpacity>
      </View>
      {showHelp ? (
        <View style={styles.helpPanel}>
          <PriestCrisisCard block={helpBlock} variant="static" />
        </View>
      ) : null}

      {isReady && status ? (
        <KeyboardAvoidingView
          style={styles.flex}
          behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        >
          <FlatList
            ref={listRef}
            style={styles.flex}
            contentContainerStyle={styles.listContent}
            data={conversation.messages}
            keyExtractor={(item) => item.id}
            renderItem={({ item }) => renderMessage(item, setOpenCitation)}
            onContentSizeChange={scrollToEnd}
            keyboardShouldPersistTaps="handled"
            ListEmptyComponent={
              <View style={styles.empty}>
                <Text style={styles.emptyText}>{EMPTY_CONVERSATION_TEXT}</Text>
                <Text style={styles.privacy}>{PRIVACY_LINE}</Text>
              </View>
            }
          />
          {conversation.error
            ? renderErrorBanner(conversation.error, {
                onRetry: handleRetry,
                label: retryLabel(retryWait),
                disabled: !canRetryNow,
              })
            : null}
          {traditionNotice ? <Text style={styles.traditionNotice}>{traditionNotice}</Text> : null}
          <PriestComposer
            value={conversation.draft}
            onChangeText={conversation.setDraft}
            onSend={conversation.send}
            onCancel={conversation.cancel}
            isPending={conversation.isPending}
            stageText={pendingStageText(conversation.stage)}
            maxChars={status.max_question_chars}
            clearCount={conversation.clearCount}
            onBusyChange={setIsVoiceBusy}
          />
        </KeyboardAvoidingView>
      ) : (
        renderStatusFallback(
          failure,
          isLoading || (status?.enabled === true && gate === 'checking'),
          () => {
            if (gate === 'failed') {
              setGate('checking');
              setGateAttempt((attempt) => attempt + 1);
            }
            void reload();
          },
        )
      )}

      <CitationSheet citation={openCitation} onClose={() => setOpenCitation(null)} />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.boothDark,
  },
  flex: {
    flex: 1,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  headerButton: {
    minWidth: MIN_TOUCH_TARGET,
    minHeight: MIN_TOUCH_TARGET,
    justifyContent: 'center',
    alignItems: 'center',
  },
  backText: {
    fontSize: typography.fontSize.xxl,
    color: colors.candleGlow,
  },
  headerTitle: {
    fontSize: typography.fontSize.lg,
    fontWeight: typography.fontWeight.semibold,
    color: colors.slate200,
  },
  clearText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.candleGlow,
  },
  clearTextDisabled: {
    color: colors.slate600,
  },
  listContent: {
    flexGrow: 1,
    padding: spacing.lg,
  },
  empty: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: spacing.lg,
  },
  emptyText: {
    fontSize: typography.fontSize.md,
    lineHeight: 24,
    color: colors.slate300,
    textAlign: 'center',
  },
  privacy: {
    marginTop: spacing.md,
    fontSize: typography.fontSize.xs,
    color: colors.slate500,
    textAlign: 'center',
  },
  errorBanner: {
    marginHorizontal: spacing.md,
    marginBottom: spacing.sm,
    padding: spacing.md,
    borderRadius: borderRadius.md,
    borderWidth: 1,
    borderColor: colors.rose400,
    backgroundColor: colors.slate800,
  },
  errorText: {
    fontSize: typography.fontSize.sm,
    color: colors.slate200,
  },
  errorAction: {
    minHeight: MIN_TOUCH_TARGET,
    justifyContent: 'center',
    alignItems: 'center',
  },
  errorActionText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.candleGlow,
  },
  errorActionTextDisabled: {
    color: colors.slate600,
  },
  helpLine: {
    marginTop: spacing.sm,
    fontSize: typography.fontSize.sm,
    color: colors.slate300,
  },
  failureHelpLine: {
    textAlign: 'center',
  },
  helpRow: {
    alignItems: 'flex-end',
    paddingHorizontal: spacing.md,
  },
  helpButton: {
    minHeight: MIN_TOUCH_TARGET,
    justifyContent: 'center',
    paddingHorizontal: spacing.sm,
  },
  helpButtonText: {
    fontSize: typography.fontSize.xs,
    color: colors.slate300,
    textDecorationLine: 'underline',
  },
  helpPanel: {
    paddingHorizontal: spacing.md,
    paddingBottom: spacing.sm,
  },
  traditionNotice: {
    marginHorizontal: spacing.md,
    marginBottom: spacing.xs,
    fontSize: typography.fontSize.xs,
    color: colors.slate400,
  },
  center: {
    flex: 1,
    justifyContent: 'center',
    padding: spacing.xl,
  },
  failureText: {
    fontSize: typography.fontSize.md,
    color: colors.slate200,
    textAlign: 'center',
  },
});
