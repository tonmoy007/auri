// Auri — Animated pulsing record button
// States: idle, recording, processing, done with visual feedback

import React, { useCallback } from 'react';
import {
  TouchableOpacity,
  View,
  Text,
  StyleSheet,
  Animated,
  Easing,
} from 'react-native';
import { colors } from '../theme/colors';
import { typography, spacing } from '../theme';
import type { ConfessionStatus } from '../types';

interface RecordButtonProps {
  /** Current recording status */
  status: ConfessionStatus;
  /** Called when recording starts */
  onStart: () => void;
  /** Called when recording stops */
  onStop: () => void;
}

/**
 * Animated pulsing record button with multi-state feedback.
 * - idle: Static button, press to start
 * - recording: Pulsing red circle with elapsed time indicator
 * - processing: Spinning indicator
 * - done: Checkmark, press to continue
 */
export function RecordButton({
  status,
  onStart,
  onStop,
}: RecordButtonProps): React.JSX.Element {
  // Animation values
  const pulseAnim = React.useRef(new Animated.Value(1)).current;
  const glowAnim = React.useRef(new Animated.Value(0)).current;

  // Start pulsing animation when recording
  React.useEffect(() => {
    if (status === 'recording') {
      const pulse = Animated.loop(
        Animated.sequence([
          Animated.timing(pulseAnim, {
            toValue: 1.15,
            duration: 800,
            easing: Easing.inOut(Easing.ease),
            useNativeDriver: true,
          }),
          Animated.timing(pulseAnim, {
            toValue: 1,
            duration: 800,
            easing: Easing.inOut(Easing.ease),
            useNativeDriver: true,
          }),
        ]),
      );

      const glow = Animated.loop(
        Animated.sequence([
          Animated.timing(glowAnim, {
            toValue: 1,
            duration: 1200,
            easing: Easing.inOut(Easing.ease),
            useNativeDriver: true,
          }),
          Animated.timing(glowAnim, {
            toValue: 0,
            duration: 1200,
            easing: Easing.inOut(Easing.ease),
            useNativeDriver: true,
          }),
        ]),
      );

      pulse.start();
      glow.start();

      return () => {
        pulse.stop();
        glow.stop();
      };
    } else {
      // Reset animations
      pulseAnim.setValue(1);
      glowAnim.setValue(0);
    }
  }, [status, pulseAnim, glowAnim]);

  const handlePress = useCallback(() => {
    if (status === 'idle') {
      onStart();
    } else if (status === 'recording') {
      onStop();
    }
    // processing and done states disable the button
  }, [status, onStart, onStop]);

  const isDisabled = status === 'processing' || status === 'done';
  const glowOpacity = glowAnim.interpolate({
    inputRange: [0, 1],
    outputRange: [0.3, 0.7],
  });

  // Render state-specific content
  const renderContent = (): React.ReactNode => {
    switch (status) {
      case 'idle':
        return <View style={styles.micIcon} />;
      case 'recording':
        return (
          <View style={styles.stopIconContainer}>
            <View style={styles.stopIcon} />
          </View>
        );
      case 'processing':
        return (
          <View style={styles.spinner}>
            <Text style={styles.spinnerText}>⋯</Text>
          </View>
        );
      case 'done':
        return (
          <View style={styles.checkmarkContainer}>
            <Text style={styles.checkmark}>✓</Text>
          </View>
        );
    }
  };

  return (
    <View style={styles.wrapper}>
      {/* Outer glow ring */}
      {status === 'recording' && (
        <Animated.View
          style={[
            styles.glowRing,
            {
              opacity: glowOpacity,
              transform: [{ scale: pulseAnim }],
            },
          ]}
        />
      )}

      {/* Main button */}
      <Animated.View
        style={[
          styles.buttonOuter,
          status === 'recording' && {
            transform: [{ scale: pulseAnim }],
          },
        ]}
      >
        <TouchableOpacity
          style={[
            styles.button,
            status === 'recording' && styles.buttonRecording,
            status === 'done' && styles.buttonDone,
            status === 'processing' && styles.buttonProcessing,
            isDisabled && styles.buttonDisabled,
          ]}
          onPress={handlePress}
          disabled={isDisabled}
          activeOpacity={0.7}
          accessibilityRole="button"
          accessibilityState={{
            disabled: isDisabled,
          }}
          accessibilityLabel={
            status === 'idle'
              ? 'Start recording'
              : status === 'recording'
                ? 'Stop recording'
                : status === 'processing'
                  ? 'Processing recording'
                  : 'Recording complete'
          }
        >
          {renderContent()}
        </TouchableOpacity>
      </Animated.View>

      {/* Status label */}
      <Text style={styles.statusLabel}>
        {status === 'idle'
          ? 'Tap to speak'
          : status === 'recording'
            ? 'Tap to stop'
            : status === 'processing'
              ? 'Masking voice…'
              : 'Complete'}
      </Text>
    </View>
  );
}

const BUTTON_SIZE = 72;
const OUTER_SIZE = 80;

const styles = StyleSheet.create({
  wrapper: {
    alignItems: 'center',
  },
  glowRing: {
    position: 'absolute',
    width: OUTER_SIZE + 20,
    height: OUTER_SIZE + 20,
    borderRadius: (OUTER_SIZE + 20) / 2,
    backgroundColor: colors.rose500,
    opacity: 0.25,
  },
  buttonOuter: {
    width: OUTER_SIZE,
    height: OUTER_SIZE,
    borderRadius: OUTER_SIZE / 2,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: `${colors.slate800}80`,
    shadowColor: colors.rose500,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.3,
    shadowRadius: 12,
    elevation: 6,
  },
  button: {
    width: BUTTON_SIZE,
    height: BUTTON_SIZE,
    borderRadius: BUTTON_SIZE / 2,
    backgroundColor: colors.rose600,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 2,
    borderColor: colors.rose400,
  },
  buttonRecording: {
    backgroundColor: colors.rose700,
    borderColor: colors.rose500,
  },
  buttonDone: {
    backgroundColor: colors.emerald600,
    borderColor: colors.emerald400,
  },
  buttonProcessing: {
    backgroundColor: colors.slate600,
    borderColor: colors.slate400,
  },
  buttonDisabled: {
    opacity: 0.6,
  },
  micIcon: {
    width: 24,
    height: 24,
    borderRadius: 4,
    backgroundColor: 'transparent',
    borderWidth: 2,
    borderColor: colors.white,
    borderBottomWidth: 0,
    borderTopLeftRadius: 12,
    borderTopRightRadius: 12,
  },
  stopIconContainer: {
    justifyContent: 'center',
    alignItems: 'center',
  },
  stopIcon: {
    width: 18,
    height: 18,
    backgroundColor: colors.white,
    borderRadius: 3,
  },
  spinner: {
    justifyContent: 'center',
    alignItems: 'center',
  },
  spinnerText: {
    fontSize: 28,
    color: colors.white,
    letterSpacing: 2,
  },
  checkmarkContainer: {
    justifyContent: 'center',
    alignItems: 'center',
  },
  checkmark: {
    fontSize: 32,
    color: colors.white,
    fontWeight: typography.fontWeight.bold,
  },
  statusLabel: {
    marginTop: spacing.sm,
    fontSize: typography.fontSize.xs,
    color: colors.slate400,
    letterSpacing: 1,
  },
});
