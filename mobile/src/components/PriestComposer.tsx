// Auri — Guide composer
// The question input: a multiline field with a character counter, a mic button
// (13.27) and a send button, plus the waiting line with Cancel. Voice input fills
// the field for the user to read and edit; it never sends anything by itself.
// There is no voice masking and no spoken reply anywhere in this feature.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AccessibilityInfo, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';
import { colors } from '../theme/colors';
import { borderRadius, spacing, typography } from '../theme';
import { ShimmerText } from './LoadingStates';
import { useAudioRecorder } from '../hooks/useAudioRecorder';
import { useHaptics } from '../hooks/useHaptics';
import { useReducedMotion } from '../hooks/useReducedMotion';
import { deleteRecordingFile } from '../lib/recordingFiles';
import {
  canSubmitQuestion,
  clampQuestion,
  classifyRecorderError,
  createGenerationGuard,
  mergeTranscript,
  questionCounter,
  voiceIssueForStart,
  voiceIssueMessage,
  type VoiceIssue,
} from '../lib/priestInput';

const MIN_TOUCH_TARGET = 44;
const MAX_INPUT_HEIGHT = 140;

interface PriestComposerProps {
  value: string;
  onChangeText: (text: string) => void;
  onSend: () => void;
  onCancel: () => void;
  isPending: boolean;
  /** The client-side waiting line ("Searching the library…"). */
  stageText: string;
  maxChars: number;
  /** Changes every time the conversation is cleared: stops recording and drops a late transcript. */
  clearCount: number;
  /** Told whether a recording or transcription is under way, so Retry can wait for it. */
  onBusyChange?: (isBusy: boolean) => void;
}

/** Question input with counter, voice input, Send, and Cancel while a request is pending. */
export function PriestComposer({
  value,
  onChangeText,
  onSend,
  onCancel,
  isPending,
  stageText,
  maxChars,
  clearCount,
  onBusyChange,
}: PriestComposerProps): React.JSX.Element {
  // Voice is optional here: no microphone prompt or audio-session change until the mic is tapped.
  const autoStopRef = useRef<(uri: string, durationMs: number) => void>();
  const recorder = useAudioRecorder({
    prepareOnMount: false,
    onAutoStop: (uri, durationMs) => autoStopRef.current?.(uri, durationMs),
  });
  const haptics = useHaptics();
  const reduceMotion = useReducedMotion();
  const [voiceIssue, setVoiceIssue] = useState<VoiceIssue | null>(null);
  const [isTranscribing, setIsTranscribing] = useState(false);
  // The transcript arrives after the user may have typed more; read the latest.
  const valueRef = useRef(value);
  valueRef.current = value;
  const isMountedRef = useRef(true);
  // Stale once Clear is tapped: a transcript that returns after that is dropped.
  const transcriptGuard = useMemo(() => createGenerationGuard(), []);
  const seenClearCountRef = useRef(clearCount);
  // Set on the tap itself, so a double tap cannot start two recordings.
  const isMicActionRef = useRef(false);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (seenClearCountRef.current === clearCount) return;
    seenClearCountRef.current = clearCount;
    transcriptGuard.bump();
    setIsTranscribing(false);
    setVoiceIssue(null);
    if (recorder.isRecording) {
      void recorder.stopRecording().then((uri) => deleteRecordingFile(uri));
    }
  }, [clearCount, recorder, transcriptGuard]);

  useEffect(() => {
    const issue = classifyRecorderError(recorder.error, recorder.hasPermission);
    if (issue) setVoiceIssue(issue);
  }, [recorder.error, recorder.hasPermission]);

  useEffect(() => {
    if (recorder.isRecording) setVoiceIssue(null);
  }, [recorder.isRecording]);

  // The message is drawn in place; a screen reader has to be told it appeared.
  useEffect(() => {
    if (voiceIssue) AccessibilityInfo.announceForAccessibility(voiceIssueMessage(voiceIssue));
  }, [voiceIssue]);

  const transcribe = useCallback(
    async (uri: string, durationMs: number, notice: VoiceIssue | null = null) => {
      const token = transcriptGuard.current();
      setIsTranscribing(true);
      try {
        // Local only: a spoken question must never be retried through a hosted provider.
        const transcript = await recorder.transcribeRecording(uri, durationMs, {
          localOnly: true,
        });
        if (!isMountedRef.current || !transcriptGuard.isCurrent(token)) return;
        if (transcript === null || !transcript.trim()) {
          setVoiceIssue('transcribe');
          return;
        }
        setVoiceIssue(notice);
        onChangeText(mergeTranscript(valueRef.current, transcript, maxChars));
      } finally {
        void deleteRecordingFile(uri);
        if (isMountedRef.current && transcriptGuard.isCurrent(token)) setIsTranscribing(false);
      }
    },
    [recorder, onChangeText, maxChars, transcriptGuard],
  );

  autoStopRef.current = (uri, durationMs) => {
    haptics.recordStop();
    void transcribe(uri, durationMs, 'limit');
  };

  const handleMicPress = useCallback(async () => {
    if (isTranscribing || isPending || isMicActionRef.current) return;
    isMicActionRef.current = true;
    try {
      if (!recorder.isRecording) {
        haptics.recordStart();
        setVoiceIssue(voiceIssueForStart(await recorder.startRecording()));
        return;
      }
      haptics.recordStop();
      const durationMs = recorder.durationMs;
      const uri = await recorder.stopRecording();
      if (!uri) {
        setVoiceIssue('record');
        return;
      }
      await transcribe(uri, durationMs);
    } finally {
      isMicActionRef.current = false;
    }
  }, [isTranscribing, isPending, recorder, haptics, transcribe]);

  const handleChangeText = useCallback(
    (text: string) => {
      setVoiceIssue(null);
      onChangeText(clampQuestion(text, maxChars));
    },
    [onChangeText, maxChars],
  );

  const counter = questionCounter(value, maxChars);
  const isBusy = recorder.isRecording || isTranscribing;

  useEffect(() => {
    onBusyChange?.(isBusy);
  }, [isBusy, onBusyChange]);

  const canSend = !isPending && !isBusy && canSubmitQuestion(value, maxChars);
  const micLabel = recorder.isRecording ? 'Stop recording' : 'Start voice input';
  const waitingStyle = [styles.status, styles.statusFlex];

  return (
    <View style={styles.container}>
      {isPending ? (
        <View style={styles.statusRow}>
          {reduceMotion ? (
            <Text style={waitingStyle}>{stageText}</Text>
          ) : (
            <ShimmerText style={waitingStyle}>{stageText}</ShimmerText>
          )}
          <TouchableOpacity
            style={styles.cancelButton}
            onPress={onCancel}
            accessibilityRole="button"
            accessibilityLabel="Cancel question"
          >
            <Text style={styles.cancelText}>Cancel</Text>
          </TouchableOpacity>
        </View>
      ) : null}
      {recorder.isRecording ? <Text style={styles.status}>Listening… tap again to stop.</Text> : null}
      {isTranscribing ? <Text style={styles.status}>Turning your voice into text…</Text> : null}
      {voiceIssue ? (
        <Text style={styles.voiceIssue} accessibilityRole="alert">
          {voiceIssueMessage(voiceIssue)}
        </Text>
      ) : null}
      <View style={styles.inputRow}>
        <TextInput
          style={styles.input}
          value={value}
          onChangeText={handleChangeText}
          editable={!isPending}
          multiline
          autoCorrect={false}
          spellCheck={false}
          autoComplete="off"
          autoCapitalize="sentences"
          importantForAutofill="no"
          textContentType="none"
          placeholder="Ask about what the library says"
          placeholderTextColor={colors.slate500}
          accessibilityLabel="Your question"
          accessibilityHint="Type a question for the Guide"
        />
        <TouchableOpacity
          style={[styles.roundButton, recorder.isRecording && styles.micActive]}
          onPress={() => void handleMicPress()}
          disabled={isPending || isTranscribing}
          accessibilityRole="button"
          accessibilityLabel={micLabel}
          accessibilityState={{ disabled: isPending || isTranscribing, busy: isTranscribing }}
        >
          <Text style={styles.roundButtonText}>{recorder.isRecording ? '■' : '🎤'}</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.roundButton, styles.sendButton, !canSend && styles.sendDisabled]}
          onPress={onSend}
          disabled={!canSend}
          accessibilityRole="button"
          accessibilityLabel="Send question"
          accessibilityState={{ disabled: !canSend }}
        >
          <Text style={styles.sendText}>↑</Text>
        </TouchableOpacity>
      </View>
      <Text
        style={[styles.counter, counter.isNearLimit && styles.counterNear]}
        accessibilityLabel={counter.accessibilityLabel}
      >
        {counter.label}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    padding: spacing.md,
    borderTopWidth: 1,
    borderTopColor: colors.slate800,
    backgroundColor: colors.boothDark,
  },
  statusRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  status: {
    marginBottom: spacing.xs,
    fontSize: typography.fontSize.sm,
    color: colors.candleGlow,
  },
  statusFlex: {
    flex: 1,
  },
  cancelButton: {
    minWidth: MIN_TOUCH_TARGET,
    minHeight: MIN_TOUCH_TARGET,
    alignItems: 'center',
    justifyContent: 'center',
  },
  cancelText: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.slate300,
  },
  voiceIssue: {
    marginBottom: spacing.xs,
    fontSize: typography.fontSize.sm,
    color: colors.rose400,
  },
  inputRow: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: spacing.sm,
  },
  input: {
    flex: 1,
    minHeight: MIN_TOUCH_TARGET,
    maxHeight: MAX_INPUT_HEIGHT,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: borderRadius.lg,
    borderWidth: 1.5,
    borderColor: colors.slate700,
    backgroundColor: colors.slate800,
    color: colors.slate200,
    fontSize: typography.fontSize.md,
  },
  roundButton: {
    width: MIN_TOUCH_TARGET,
    height: MIN_TOUCH_TARGET,
    borderRadius: MIN_TOUCH_TARGET / 2,
    borderWidth: 1.5,
    borderColor: colors.slate600,
    alignItems: 'center',
    justifyContent: 'center',
  },
  roundButtonText: {
    fontSize: typography.fontSize.lg,
    color: colors.slate200,
  },
  micActive: {
    borderColor: colors.rose500,
    backgroundColor: colors.rose600,
  },
  sendButton: {
    borderColor: colors.candleGlow,
    backgroundColor: colors.candleGlow,
  },
  sendDisabled: {
    borderColor: colors.slate600,
    backgroundColor: colors.slate700,
  },
  sendText: {
    fontSize: typography.fontSize.xl,
    fontWeight: typography.fontWeight.bold,
    color: colors.boothDark,
  },
  counter: {
    marginTop: spacing.xs,
    alignSelf: 'flex-end',
    fontSize: typography.fontSize.xs,
    color: colors.slate500,
  },
  counterNear: {
    color: colors.candleGlow,
  },
});
