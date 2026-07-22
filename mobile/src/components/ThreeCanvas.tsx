// Auri — Reusable React Three Fiber Canvas wrapper
// Provides environment controls and consistent configuration for all 3D scenes

import React, { useRef, Suspense, useMemo } from 'react';
import { View, StyleSheet, Text } from 'react-native';
import { Canvas, useFrame } from '@react-three/fiber';
import { AdaptiveDpr, AdaptiveEvents } from '@react-three/drei';
import { Color, Group } from 'three';
import { colors } from '../theme/colors';
import type { Environment as EnvironmentType } from '../types';

interface ThreeCanvasProps {
  /** Environment preset for lighting and background */
  environment?: EnvironmentType;
  /** Whether to enable performance optimizations for mobile */
  mobileOptimized?: boolean;
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

          {/* Auto-rotate camera for atmospheric effect */}
          <CameraRotator />
        </Canvas>
      </Suspense>
    </View>
  );
}

/**
 * Subtle camera rotation for ambient atmosphere.
 * Only rotates slightly to give a sense of depth.
 */
function CameraRotator(): null {
  const groupRef = useRef<Group>(null);

  useFrame((_state, delta) => {
    if (groupRef.current) {
      groupRef.current.rotation.y += delta * 0.05;
    }
  });

  useFrame((state) => {
    const t = state.clock.elapsedTime;
    state.camera.position.x = Math.sin(t * 0.05) * 0.3;
    state.camera.position.y = Math.sin(t * 0.03) * 0.1 + 0.5;
    state.camera.lookAt(0, 0, 0);
  });

  return null;
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
