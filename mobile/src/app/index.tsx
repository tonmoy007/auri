// Auri — Home screen
// Entry point with 'Enter Auri' button, 3D background preview, and tagline

import React, { useCallback } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  Dimensions,
} from 'react-native';
import { router } from 'expo-router';
import { colors } from '../theme/colors';
import { typography, spacing } from '../theme';
import { ThreeCanvas } from '../components/ThreeCanvas';

const { width, height } = Dimensions.get('window');

/**
 * Home screen — landing page for the confession booth experience.
 * Displays a 3D atmospheric background with a prominent call-to-action.
 */
export default function HomeScreen(): React.JSX.Element {
  const handleEnterAuri = useCallback(() => {
    const sessionId = generateSessionId();
    router.push(`/confession/${sessionId}`);
  }, []);

  return (
    <View style={styles.container}>
      {/* 3D atmospheric background */}
      <View style={styles.threeContainer}>
        <ThreeCanvas />
      </View>

      {/* Overlay content */}
      <View style={styles.overlay}>
        <View style={styles.titleContainer}>
          <Text style={styles.title}>Auri</Text>
          <Text style={styles.tagline}>
            Speak freely. Be heard. Remain unknown.
          </Text>
        </View>

        <TouchableOpacity
          style={styles.enterButton}
          onPress={handleEnterAuri}
          activeOpacity={0.8}
          accessibilityRole="button"
          accessibilityLabel="Enter the confession booth"
        >
          <Text style={styles.enterButtonText}>Enter Auri</Text>
        </TouchableOpacity>

        <Text style={styles.disclaimer}>
          Your voice is anonymized. No identity is stored.
        </Text>
      </View>
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
});
