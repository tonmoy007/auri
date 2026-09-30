// Auri — Root layout with Stack navigator and dark theme configuration

import React, { useEffect, useState } from 'react';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { colors } from '../theme/colors';
import { loadBackendUrlOverride } from '../config/api';
import { purgeRecordingCache } from '../lib/recordingFiles';

/**
 * Root layout for the Expo Router app.
 * Configures navigation theme, stack transitions, and status bar appearance.
 */
export default function RootLayout(): React.JSX.Element | null {
  const [backendUrlLoaded, setBackendUrlLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    loadBackendUrlOverride().finally(() => {
      if (!cancelled) setBackendUrlLoaded(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Recordings the screens did not get to delete (a force-closed app, a back
  // gesture out of review) would otherwise stay in the cache for ever. Nothing
  // is in flight at a cold start, so whatever is found is leftover.
  useEffect(() => {
    void purgeRecordingCache();
  }, []);

  // Every screen fetches via config/api.ts's getApiBaseUrl()/getWsUrl(),
  // which read a runtime override hydrated here — wait for it so the first
  // fetch doesn't race the secure-store read and use the stale build-time
  // default for one frame.
  if (!backendUrlLoaded) return null;

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
          name="home"
          options={{
            title: 'History',
            animation: 'slide_from_left',
          }}
        />
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
            animation: 'fade',
            animationDuration: 350,
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
          name="response"
          options={{
            title: 'You Are Heard',
            animation: 'fade',
          }}
        />
        <Stack.Screen
          name="anonymity"
          options={{
            title: 'Anonymity',
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
