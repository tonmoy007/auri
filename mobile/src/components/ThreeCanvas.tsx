// Auri — Reusable React Three Fiber Canvas wrapper
// Provides environment controls and consistent configuration for all 3D scenes

import React, { Suspense, useMemo } from 'react';
import { View, StyleSheet, Text } from 'react-native';
import { Canvas } from '@react-three/fiber';
import { AdaptiveDpr, AdaptiveEvents } from '@react-three/drei';
import { Color } from 'three';
import { colors } from '../theme/colors';
import { BoothCamera } from './ThreeBooth/BoothCamera';
import type { Environment as EnvironmentType } from '../types';

interface ThreeCanvasProps {
  /** Environment preset for lighting and background */
  environment?: EnvironmentType;
  /** Whether to enable performance optimizations for mobile */
  mobileOptimized?: boolean;
  /** Dollies the camera in toward the booth while true. */
  isRecording?: boolean;
  /** Children to render inside the canvas */
  children?: React.ReactNode;
}

/**
 * Reusable 3D canvas with environment controls.
 * Handles loading states, performance adaptation, and consistent lighting.
 */
export function ThreeCanvas({
  environment = 'classic',
  mobileOptimized = true,
  isRecording = false,
  children,
}: ThreeCanvasProps): React.JSX.Element {
  const sceneBackground = useMemo(() => {
    switch (environment) {
      case 'forest':
        return new Color('#0a1f0a');
      case 'rooftop':
        return new Color('#1a1a2e');
      case 'classic':
      default:
        return new Color(colors.boothDark);
    }
  }, [environment]);

  return (
    <View style={styles.container}>
      <Suspense
        fallback={
          <View style={styles.loading}>
            <Text style={styles.loadingText}>Loading sanctuary…</Text>
          </View>
        }
      >
        <Canvas
          camera={{
            position: [0, 0, 5],
            fov: 60,
            near: 0.1,
            far: 100,
          }}
          gl={{
            antialias: !mobileOptimized,
            alpha: true,
            powerPreference: 'high-performance',
            // expo-gl's WebGL shim doesn't implement getShaderPrecisionFormat,
            // which three.js's WebGLCapabilities calls to auto-detect precision
            // for 'highp'/'mediump' — crashes with "Cannot read property
            // 'precision' of undefined" on native. 'lowp' is the only value
            // that skips that call entirely; visually indistinguishable for
            // this scene's simple materials.
            precision: 'lowp',
          }}
          dpr={mobileOptimized ? [1, 1.5] : [1, 2]}
          style={styles.canvas}
          onCreated={(state) => {
            // expo-gl's WebGL shim returns `undefined` (not an empty string)
            // from getShaderInfoLog/getProgramInfoLog when there's no compile
            // error. three.js calls `.trim()` on the result unconditionally,
            // so every shader/program compile crashes with "Cannot read
            // property 'trim' of undefined" on native. Patch both to fall
            // back to '', matching what the WebGL spec actually guarantees.
            const ctx = state.gl.getContext() as unknown as {
              getShaderInfoLog: (shader: unknown) => string | null;
              getProgramInfoLog: (program: unknown) => string | null;
            };
            const originalGetShaderInfoLog = ctx.getShaderInfoLog.bind(ctx);
            const originalGetProgramInfoLog = ctx.getProgramInfoLog.bind(ctx);
            ctx.getShaderInfoLog = (shader) => originalGetShaderInfoLog(shader) ?? '';
            ctx.getProgramInfoLog = (program) => originalGetProgramInfoLog(program) ?? '';
          }}
        >
          {/* Scene background */}
          <color attach="background" args={[sceneBackground]} />

          {/* Ambient fill light */}
          <ambientLight intensity={0.3} />
          <directionalLight position={[5, 5, 5]} intensity={0.5} />
          <pointLight position={[0, 2, 2]} intensity={0.6} color="#f59e0b" />

          {/* Performance optimizations */}
          <AdaptiveDpr pixelated />
          <AdaptiveEvents />

          {/* Scene content */}
          {children}

          {/* Booth camera rig — idle drift, dollies in while recording */}
          <BoothCamera isRecording={isRecording} />
        </Canvas>
      </Suspense>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  canvas: {
    flex: 1,
  },
  loading: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: colors.boothDark,
  },
  loadingText: {
    color: colors.slate500,
    fontSize: 14,
    letterSpacing: 1,
  },
});
