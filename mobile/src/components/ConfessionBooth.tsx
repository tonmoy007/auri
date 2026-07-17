// Auri — 3D confession booth scene
// Candle, particles, rings, door with environment cycling support

import React, { useRef, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import { Ring, Text } from '@react-three/drei';
import { Group } from 'three';
import { Candle } from './Candle';
import { colors } from '../theme/colors';
import type { Environment } from '../types';

interface ConfessionBoothProps {
  /** Current environment preset */
  environment: Environment;
}

/**
 * 3D confession booth scene.
 * Composes candle, floating particles, circular rings, and a stylized door.
 * Environment switching changes lighting and background color.
 */
export function ConfessionBooth({
  environment,
}: ConfessionBoothProps): React.JSX.Element {
  const sceneRef = useRef<Group>(null);

  // Dynamic light color based on environment
  const ambientColor = useMemo(() => {
    switch (environment) {
      case 'forest':
        return '#1a4a1a';
      case 'rooftop':
        return '#2a1a4a';
      case 'classic':
      default:
        return '#1a1a2e';
    }
  }, [environment]);

  return (
    <group ref={sceneRef}>
      {/* Ambient environment light */}
      <ambientLight color={ambientColor} intensity={0.4} />

      {/* Central candle — the main light source */}
      <Candle position={[0, -1.5, 0]} environment={environment} />

      {/* Entrance door — stylized arch */}
      <Door position={[0, -1.2, -2]} />

      {/* Floating atmospheric rings */}
      <FloatingRings />

      {/* Particle system for ambient dust/sparks */}
      <Particles count={60} environment={environment} />

      {/* Booth label */}
      <Text
        position={[0, 2.5, -1.5]}
        fontSize={0.15}
        color={colors.slate500}
        anchorX="center"
        anchorY="middle"
        letterSpacing={0.2}
      >
        AURI
      </Text>
    </group>
  );
}

/**
 * Stylized arched door using drei primitives.
 */
function Door({ position }: { position: [number, number, number] }): React.JSX.Element {
  return (
    <group position={position}>
      {/* Arch top */}
      <mesh position={[0, 0.8, 0]}>
        <torusGeometry args={[0.6, 0.05, 16, 32, Math.PI]} />
        <meshStandardMaterial color={colors.slate700} metalness={0.3} roughness={0.7} />
      </mesh>
      {/* Left pillar */}
      <mesh position={[-0.6, 0.4, 0]}>
        <boxGeometry args={[0.08, 0.8, 0.08]} />
        <meshStandardMaterial color={colors.slate700} metalness={0.3} roughness={0.7} />
      </mesh>
      {/* Right pillar */}
      <mesh position={[0.6, 0.4, 0]}>
        <boxGeometry args={[0.08, 0.8, 0.08]} />
        <meshStandardMaterial color={colors.slate700} metalness={0.3} roughness={0.7} />
      </mesh>
    </group>
  );
}

/**
 * Floating, rotating rings for visual atmosphere.
 */
function FloatingRings(): React.JSX.Element {
  return (
    <group position={[0, 0.5, -1]}>
      <Ring
        args={[0.8, 1, 32]}
        position={[0, 0, 0]}
        scale={[1.5, 1.5, 1.5]}
      >
        <meshBasicMaterial color={colors.candleGlow} transparent opacity={0.4} />
      </Ring>
    </group>
  );
}

/**
 * Particle system — floating dust motes with subtle animation.
 * Count is reduced on mobile for performance.
 */
function Particles({
  count = 60,
  environment,
}: {
  count: number;
  environment: Environment;
}): React.JSX.Element {
  const particlesRef = useRef<Group>(null);

  // Generate random initial positions
  const positions = useMemo(() => {
    const pos: [number, number, number][] = [];
    for (let i = 0; i < count; i++) {
      const x = (Math.random() - 0.5) * 4;
      const y = (Math.random() - 0.5) * 4;
      const z = (Math.random() - 0.5) * 3 - 1;
      pos.push([x, y, z]);
    }
    return pos;
  }, [count]);

  const particleColor = useMemo(() => {
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

  useFrame(() => {
    if (particlesRef.current) {
      particlesRef.current.rotation.y += 0.0005;
    }
  });

  return (
    <group ref={particlesRef}>
      {positions.map((pos, index) => (
        <mesh key={index} position={pos}>
          <sphereGeometry args={[0.02, 6, 6]} />
          <meshBasicMaterial
            color={particleColor}
            transparent
            opacity={0.3 + Math.random() * 0.4}
          />
        </mesh>
      ))}
    </group>
  );
}
