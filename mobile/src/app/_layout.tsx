// Auri — Root layout with Stack navigator and dark theme configuration

import React from 'react';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { colors } from '../theme/colors';

/**
 * Root layout for the Expo Router app.
 * Configures navigation theme, stack transitions, and status bar appearance.
 */
export default function RootLayout(): React.JSX.Element {
  return (
    <>
      <StatusBar style="light" />
      <Stack
        screenOptions={{
          headerShown: false,
          contentStyle: { backgroundColor: colors.boothDark },
          animation: 'fade',
          animationDuration: 300,
          navigationBarColor: colors.boothDark,
          statusBarStyle: 'light',
        }}
      >
        <Stack.Screen name="index" options={{ title: 'Auri' }} />
        <Stack.Screen
          name="confession/[id]"
          options={{
            title: 'Confession',
            animation: 'slide_from_bottom',
          }}
        />
        <Stack.Screen
          name="review"
          options={{
            title: 'Review',
            animation: 'slide_from_bottom',
          }}
        />
        <Stack.Screen
          name="settings"
          options={{
            title: 'Settings',
            animation: 'slide_from_right',
          }}
        />
        <Stack.Screen
          name="forward/[id]"
          options={{
            title: 'Forward',
            animation: 'slide_from_bottom',
          }}
        />
        <Stack.Screen
          name="delete-confirmation"
          options={{
            title: 'Delete',
            animation: 'fade',
          }}
        />
      </Stack>
    </>
  );
}
