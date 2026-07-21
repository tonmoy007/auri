// Auri — Animated candle with flame mesh, particle glow, wax drip effect
// The candle serves as the primary light source and visual anchor in the booth

import React, { useRef, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import { Float } from '@react-three/drei';
import { Group, Mesh, PointLight } from 'three';
import type { Environment } from '../types';

interface CandleProps {
  /** World position of the candle */
  position: [number, number, number];
  /** Current environment for color adaptation */
  environment: Environment;
  /** Pulses faster and brighter while the confession is being processed */
  isProcessing?: boolean;
}

/**
 * Animated candle with:
 * - Wax body with subtle wobble
 * - Flame mesh with heat shimmer
 * - Glow particles rising from the flame
 * - Wax drip particle effect
 * - Dynamic point light source
 */
export function Candle({
  position,
  environment,
  isProcessing = false,
}: CandleProps): React.JSX.Element {
  const groupRef = useRef<Group>(null);
  const flameRef = useRef<Mesh>(null);
  const lightRef = useRef<PointLight>(null);
  const glowRef = useRef<Group>(null);

  // Flame color adapts to environment
  const flameColor = useMemo(() => {
    switch (environment) {
      case 'forest':
        return '#4ade80';
      case 'rooftop':
        return '#a78bfa';
      case 'classic':
      default:
        return '#f59e0b';
    }
  }, [environment]);

  const glowColor = useMemo(() => {
    switch (environment) {
      case 'forest':
        return '#22c55e';
      case 'rooftop':
        return '#8b5cf6';
      case 'classic':
      default:
        return '#f59e0b';
    }
  }, [environment]);

  // Animate flame flicker and glow
  useFrame((state) => {
    const t = state.clock.elapsedTime;

    // Flame flicker: squash and stretch
    if (flameRef.current) {
      const flicker = Math.sin(t * 10) * 0.05 + Math.cos(t * 7.3) * 0.03;
      const scaleY = 1 + flicker * 2;
      const scaleX = 1 + Math.sin(t * 12) * 0.04;
      flameRef.current.scale.x = scaleX;
      flameRef.current.scale.y = scaleY;
    }

    // Point light intensity pulsation — faster and brighter while processing
    if (lightRef.current) {
      const pulseSpeed = isProcessing ? 4 : 1;
      const baseIntensity = isProcessing ? 0.9 : 0.6;
      const amplitude = isProcessing ? 0.35 : 0.15;
      const intensity =
        baseIntensity +
        Math.sin(t * 4 * pulseSpeed) * amplitude +
        Math.cos(t * 3.7 * pulseSpeed) * 0.1;
      lightRef.current.intensity = Math.max(0.3, intensity);
    }

    // Glow particles rotation
    if (glowRef.current) {
      glowRef.current.rotation.y += 0.01;
    }
  });

  // Generate glow particle positions
  const glowParticles = useMemo(() => {
    const particles: [number, number, number][] = [];
    for (let i = 0; i < 8; i++) {
      const angle = (i / 8) * Math.PI * 2;
      const radius = 0.15 + Math.random() * 0.1;
      const x = Math.cos(angle) * radius;
      const z = Math.sin(angle) * radius;
      const y = 0.3 + Math.random() * 0.2;
      particles.push([x, y, z]);
    }
    return particles;
  }, []);

  return (
    <group ref={groupRef} position={position}>
      {/* Wax body */}
      <Float speed={0.5} rotationIntensity={0.02} floatIntensity={0.02}>
        <mesh position={[0, 0, 0]}>
          <cylinderGeometry args={[0.08, 0.1, 0.4, 12]} />
          <meshStandardMaterial
            color="#f5f0e8"
            roughness={0.9}
            metalness={0.0}
          />
        </mesh>

        {/* Wax drip details */}
        {[-0.05, 0.05].map((xOffset, index) => (
          <mesh key={`drip-${index}`} position={[xOffset, -0.15, 0.06]}>
            <sphereGeometry args={[0.02, 6, 6]} />
            <meshStandardMaterial
              color="#f0ebe0"
              roughness={0.8}
            />
          </mesh>
        ))}
      </Float>

      {/* Flame */}
      <mesh ref={flameRef} position={[0, 0.25, 0]}>
        <coneGeometry args={[0.06, 0.15, 8]} />
        <meshStandardMaterial
          color={flameColor}
          emissive={flameColor}
          emissiveIntensity={0.8}
          transparent
          opacity={0.9}
        />
      </mesh>

      {/* Inner flame (hotter core) */}
      <mesh position={[0, 0.27, 0]} scale={[0.3, 0.4, 0.3]}>
        <coneGeometry args={[0.06, 0.15, 8]} />
        <meshStandardMaterial
          color="#fef08a"
          emissive="#fef08a"
          emissiveIntensity={1.0}
          transparent
          opacity={0.6}
        />
      </mesh>

      {/* Glow particles */}
      <group ref={glowRef}>
        {glowParticles.map((pos, index) => (
          <mesh key={index} position={pos}>
            <sphereGeometry args={[0.01, 4, 4]} />
            <meshBasicMaterial
              color={glowColor}
              transparent
              opacity={0.4}
            />
          </mesh>
        ))}
      </group>

      {/* Dynamic point light */}
      <pointLight
        ref={lightRef}
        position={[0, 0.5, 0]}
        color={flameColor}
        intensity={0.6}
        distance={3}
        decay={2}
      />
    </group>
  );
}
