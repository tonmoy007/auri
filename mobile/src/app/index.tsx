// Auri — Home screen
// Entry point with 'Enter Auri' button, 3D background preview, and tagline

import React, { useCallback, useState, useRef } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  Dimensions,
  Animated,
} from 'react-native';
import { router, useFocusEffect } from 'expo-router';
import { colors } from '../theme/colors';
import { typography, spacing } from '../theme';
import { ThreeCanvas } from '../components/ThreeCanvas';
import { useHaptics } from '../hooks/useHaptics';
import { usePriestStatus } from '../hooks/usePriestStatus';
import { hasAcknowledgedPriestIntro, useSettings } from '../hooks/useSettings';
import {
  guideEntryAccessibilityLabel,
  guideEntryLabel,
  shouldShowGuideEntry,
} from '../lib/priestInput';

const { width, height } = Dimensions.get('window');

/** Booth entry fade-to-black duration, ms — long enough to mask the 3D scene mount cost. */
const ENTRY_FADE_MS = 350;

/**
 * Home screen — landing page for the confession booth experience.
 * Displays a 3D atmospheric background with a prominent call-to-action.
 */
export default function HomeScreen(): React.JSX.Element {
  const haptics = useHaptics();
  const fadeToBlack = useRef(new Animated.Value(0)).current;
  const { status: guideStatus, reload: reloadGuideStatus } = usePriestStatus(false);
  const { showGuideMode, reload: reloadSettings } = useSettings();
  // Set on the tap, cleared when the screen regains focus: a double tap must not stack two Guide screens.
  const isOpeningGuideRef = useRef(false);

  // The fade-to-black overlay is mounted only while the booth entry
  // transition is actually running.
  //
  // It used to be mounted permanently with its opacity driven by a
  // native-driver animated value, and reset with `setValue(0)`. That reset
  // is unreliable here: while this screen sits under the booth its animated
  // node is detached, so a `setValue` from JS does not always reach the
  // native view — and resetting again on focus did not fix it either. The
  // screen came back fully black but still tappable, which is indis-
  // tinguishable from the app having died. Unmounting the overlay removes
  // the failure mode instead of trying to out-race it: an overlay that is
  // not rendered cannot black the screen out, whatever the animated value
  // happens to hold.
  const [isEnteringBooth, setIsEnteringBooth] = useState(false);

  useFocusEffect(
    useCallback(() => {
      setIsEnteringBooth(false);
      isOpeningGuideRef.current = false;
      fadeToBlack.setValue(0);
      // Settings may have changed the Guide toggle, and the server may have
      // switched the Guide on or off, while this screen sat under another.
      void reloadGuideStatus();
      void reloadSettings();
    }, [fadeToBlack, reloadGuideStatus, reloadSettings]),
  );

  const handleEnterAuri = useCallback(() => {
    haptics.selectionChanged();
    fadeToBlack.setValue(0);
    setIsEnteringBooth(true);
    Animated.timing(fadeToBlack, {
      toValue: 1,
      duration: ENTRY_FADE_MS,
      useNativeDriver: true,
    }).start(({ finished }) => {
      if (!finished) return;
      const sessionId = generateSessionId();
      router.push(`/confession/${sessionId}`);
      // Reset for the next visit to this screen (e.g. after going back).
      fadeToBlack.setValue(0);
    });
  }, [fadeToBlack, haptics]);

  const handleOpenGuide = useCallback(async () => {
    if (isOpeningGuideRef.current) return;
    isOpeningGuideRef.current = true;
    haptics.selectionChanged();
    let acknowledged = false;
    try {
      acknowledged = guideStatus
        ? await hasAcknowledgedPriestIntro(guideStatus.disclaimer_version)
        : false;
    } catch (_error: unknown) {
      // An unreadable store counts as not yet acknowledged: the intro is the safe screen to show.
      acknowledged = false;
    }
    router.push(acknowledged ? '/priest' : '/priest/intro');
  }, [guideStatus, haptics]);

  const handleOpenSettings = useCallback(() => {
    router.push('/settings');
  }, []);

  const handleOpenHistory = useCallback(() => {
    router.push('/home');
  }, []);

  return (
    <View style={styles.container}>
      {/* 3D atmospheric background */}
      <View style={styles.threeContainer}>
        <ThreeCanvas />
      </View>

      {/* History entry point */}
      <TouchableOpacity
        style={styles.historyButton}
        onPress={handleOpenHistory}
        accessibilityRole="button"
        accessibilityLabel="View confession history"
        hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}
      >
        <Text style={styles.settingsIcon}>☰</Text>
      </TouchableOpacity>

      {/* Settings entry point */}
      <TouchableOpacity
        style={styles.settingsButton}
        onPress={handleOpenSettings}
        accessibilityRole="button"
        accessibilityLabel="Open settings"
        hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}
      >
        <Text style={styles.settingsIcon}>⚙</Text>
      </TouchableOpacity>

      {/* Overlay content */}
      <View style={styles.overlay}>
        <View style={styles.titleContainer}>
          <Text style={styles.title}>Auri</Text>
          <Text style={styles.tagline}>
            Speak freely. Be heard. Remain unknown.
          </Text>
        </View>

        <View style={styles.ctaGroup}>
          <TouchableOpacity
            style={styles.enterButton}
            onPress={handleEnterAuri}
            activeOpacity={0.8}
            accessibilityRole="button"
            accessibilityLabel="Enter the confession booth"
          >
            <Text style={styles.enterButtonText}>Enter Auri</Text>
          </TouchableOpacity>
          {guideStatus && shouldShowGuideEntry(guideStatus, showGuideMode) ? (
            <TouchableOpacity
              style={styles.guideButton}
              onPress={() => void handleOpenGuide()}
              activeOpacity={0.8}
              accessibilityRole="button"
              accessibilityLabel={guideEntryAccessibilityLabel(guideStatus.persona_name)}
            >
              <Text style={styles.guideButtonText}>{guideEntryLabel()}</Text>
            </TouchableOpacity>
          ) : null}
        </View>

        <Text style={styles.disclaimer}>
          Your voice is masked. No name is asked for.
        </Text>
      </View>

      {/* Booth entry transition — fades to black before the booth screen
          mounts, and is unmounted the moment this screen is focused again. */}
      {isEnteringBooth && (
        <Animated.View
          pointerEvents="none"
          style={[styles.entryOverlay, { opacity: fadeToBlack }]}
        />
      )}
    </View>
  );
}

/**
 * Generate a unique session identifier.
 * Uses timestamp + random string for sufficient uniqueness.
 */
function generateSessionId(): string {
  const timestamp = Date.now().toString(36);
  const random = Math.random().toString(36).substring(2, 8);
  return `${timestamp}-${random}`;
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.boothDark,
  },
  threeContainer: {
    ...StyleSheet.absoluteFillObject,
    zIndex: 0,
  },
  settingsButton: {
    position: 'absolute',
    top: 56,
    right: spacing.lg,
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: 'rgba(15, 23, 42, 0.6)',
    justifyContent: 'center',
    alignItems: 'center',
    zIndex: 2,
  },
  historyButton: {
    position: 'absolute',
    top: 56,
    left: spacing.lg,
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: 'rgba(15, 23, 42, 0.6)',
    justifyContent: 'center',
    alignItems: 'center',
    zIndex: 2,
  },
  settingsIcon: {
    fontSize: typography.fontSize.xl,
    color: colors.slate300,
  },
  overlay: {
    flex: 1,
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingTop: height * 0.15,
    paddingBottom: spacing.xl,
    paddingHorizontal: spacing.lg,
    zIndex: 1,
  },
  titleContainer: {
    alignItems: 'center',
  },
  title: {
    fontSize: typography.fontSize.hero,
    fontWeight: typography.fontWeight.bold,
    color: colors.candleGlow,
    letterSpacing: 8,
    textTransform: 'uppercase',
    textShadowColor: 'rgba(245, 158, 11, 0.4)',
    textShadowOffset: { width: 0, height: 0 },
    textShadowRadius: 20,
  },
  tagline: {
    fontSize: typography.fontSize.md,
    color: colors.slate300,
    textAlign: 'center',
    marginTop: spacing.md,
    fontStyle: 'italic',
    letterSpacing: 1,
  },
  enterButton: {
    backgroundColor: colors.candleGlow,
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.xxl,
    borderRadius: 50,
    minWidth: width * 0.6,
    alignItems: 'center',
    shadowColor: colors.candleGlow,
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.4,
    shadowRadius: 16,
    elevation: 8,
  },
  ctaGroup: {
    alignItems: 'center',
    gap: spacing.md,
  },
  guideButton: {
    minHeight: 44,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.xl,
    borderRadius: 50,
    borderWidth: 1.5,
    borderColor: colors.candleGlow,
    backgroundColor: 'rgba(15, 23, 42, 0.6)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  guideButtonText: {
    fontSize: typography.fontSize.md,
    fontWeight: typography.fontWeight.semibold,
    color: colors.candleGlow,
    letterSpacing: 1,
  },
  enterButtonText: {
    fontSize: typography.fontSize.lg,
    fontWeight: typography.fontWeight.semibold,
    color: colors.boothDark,
    letterSpacing: 2,
    textTransform: 'uppercase',
  },
  disclaimer: {
    fontSize: typography.fontSize.xs,
    color: colors.slate500,
    textAlign: 'center',
    maxWidth: width * 0.7,
  },
  entryOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: colors.boothDark,
    zIndex: 3,
  },
});
