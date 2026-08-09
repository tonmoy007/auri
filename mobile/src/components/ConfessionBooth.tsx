// Auri — 3D confession booth scene
// Candle, particles, rings, door with environment cycling support

import React, { useRef, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import { Ring } from '@react-three/drei';
import { Group, Mesh, MathUtils } from 'three';
import { Candle } from './Candle';
import { EnvironmentBackdrop } from './environments';
import { colors } from '../theme/colors';
import type { Environment } from '../types';

interface ConfessionBoothProps {
  /** Current environment preset */
  environment: Environment;
  /** Pulses the candle faster and brighter while the confession is being processed */
  isProcessing?: boolean;
  /** 0 = full flame, 1 = fully extinguished — passed through to the candle */
  extinguishProgress?: number;
  /** Whether the booth door is swung open. Defaults open for screens that don't drive entry/exit. */
  doorOpen?: boolean;
  /** Live mic input level, normalized 0-1 — pulses the ambient rings while recording. */
  amplitude?: number;
}

/**
 * 3D confession booth scene.
 * Composes candle, floating particles, circular rings, and a stylized door.
 * Environment switching changes lighting and background color.
 */
export function ConfessionBooth({
  environment,
  isProcessing = false,
  extinguishProgress = 0,
  doorOpen = true,
  amplitude = 0,
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
      <Candle
        position={[0, -1.5, 0]}
        environment={environment}
        isProcessing={isProcessing}
        extinguishProgress={extinguishProgress}
      />

      {/* Entrance door — stylized arch */}
      <Door position={[0, -1.2, -2]} open={doorOpen} />

      {/* Floating atmospheric rings — pulse with mic amplitude while recording */}
      <FloatingRings amplitude={amplitude} />

      {/* Particle system for ambient dust/sparks */}
      <Particles count={60} environment={environment} />

      {/* Environment-specific scenery — trees/fireflies, night sky/skyline, etc. */}
      <EnvironmentBackdrop environment={environment} />
    </group>
  );
}

/** Door panel's swing target, radians — pivots open around the left hinge pillar. */
const DOOR_OPEN_ANGLE = -Math.PI / 1.8;
/** Higher = door swings faster; tuned so open/close settles in ~500-600ms. */
const DOOR_SWING_SPEED = 4;

/**
 * Stylized arched door using drei primitives.
 * The frame (arch + pillars) is static; the panel swings open/close on the left hinge.
 */
function Door({
  position,
  open,
}: {
  position: [number, number, number];
  open: boolean;
}): React.JSX.Element {
  const panelRef = useRef<Group>(null);

  useFrame((_state, delta) => {
    if (!panelRef.current) return;
    const target = open ? DOOR_OPEN_ANGLE : 0;
    panelRef.current.rotation.y = MathUtils.lerp(
      panelRef.current.rotation.y,
      target,
      Math.min(delta * DOOR_SWING_SPEED, 1),
    );
  });

  return (
    <group position={position}>
      {/* Arch top */}
      <mesh position={[0, 0.8, 0]}>
        <torusGeometry args={[0.6, 0.05, 16, 32, Math.PI]} />
        <meshStandardMaterial color={colors.slate700} metalness={0.3} roughness={0.7} />
      </mesh>
      {/* Left pillar — also the door's hinge post */}
      <mesh position={[-0.6, 0.4, 0]}>
        <boxGeometry args={[0.08, 0.8, 0.08]} />
        <meshStandardMaterial color={colors.slate700} metalness={0.3} roughness={0.7} />
      </mesh>
      {/* Right pillar */}
      <mesh position={[0.6, 0.4, 0]}>
        <boxGeometry args={[0.08, 0.8, 0.08]} />
        <meshStandardMaterial color={colors.slate700} metalness={0.3} roughness={0.7} />
      </mesh>
      {/* Door panel — swings open/closed around the left pillar */}
      <group ref={panelRef} position={[-0.6, 0, 0]}>
        <mesh position={[0.5, 0.4, 0]}>
          <boxGeometry args={[1, 0.75, 0.04]} />
          <meshStandardMaterial color={colors.slate800} metalness={0.2} roughness={0.8} />
        </mesh>
      </group>
    </group>
  );
}

/** How quickly the rings' pulse chases the (choppy, ~100ms-sampled) amplitude signal — higher = snappier. */
const RING_SMOOTHING_SPEED = 6;

/**
 * Voice-responsive rings for visual atmosphere.
 * Three concentric rings breathe gently at idle and pulse outward with mic
 * amplitude while recording — the smoothing lerp keeps the pulse fluid
 * instead of stepping with each ~100ms metering sample.
 */
function FloatingRings({ amplitude }: { amplitude: number }): React.JSX.Element {
  const ring1Ref = useRef<Mesh>(null);
  const ring2Ref = useRef<Mesh>(null);
  const ring3Ref = useRef<Mesh>(null);
  const smoothedAmplitude = useRef(0);

  useFrame((state, delta) => {
    smoothedAmplitude.current = MathUtils.lerp(
      smoothedAmplitude.current,
      amplitude,
      Math.min(delta * RING_SMOOTHING_SPEED, 1),
    );
    const pulse = smoothedAmplitude.current;
    const elapsed = state.clock.elapsedTime;

    ring1Ref.current?.scale.setScalar(1.5 + pulse * 0.6);
    ring2Ref.current?.scale.setScalar(1.85 + pulse * 0.9 + Math.sin(elapsed * 1.3) * 0.04);
    ring3Ref.current?.scale.setScalar(2.2 + pulse * 1.3 + Math.sin(elapsed * 1.7 + 1) * 0.05);
  });

  return (
    <group position={[0, 0.5, -1]}>
      <Ring ref={ring1Ref} args={[0.8, 1, 32]} scale={[1.5, 1.5, 1.5]}>
        <meshBasicMaterial color={colors.candleGlow} transparent opacity={0.4} />
      </Ring>
      <Ring ref={ring2Ref} args={[0.8, 0.93, 32]} scale={[1.85, 1.85, 1.85]}>
        <meshBasicMaterial color={colors.candleGlow} transparent opacity={0.25} />
      </Ring>
      <Ring ref={ring3Ref} args={[0.8, 0.88, 32]} scale={[2.2, 2.2, 2.2]}>
        <meshBasicMaterial color={colors.candleGlow} transparent opacity={0.15} />
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
  const meshRefs = useRef<(Mesh | null)[]>([]);

  // Generate random initial (base) positions
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

  // Per-particle drift parameters so the float isn't uniform/robotic
  const floatParams = useMemo(
    () =>
      positions.map(() => ({
        speed: 0.3 + Math.random() * 0.5,
        phase: Math.random() * Math.PI * 2,
        amplitude: 0.15 + Math.random() * 0.15,
      })),
    [positions],
  );

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

  useFrame((state) => {
    if (particlesRef.current) {
      particlesRef.current.rotation.y += 0.0005;
    }
    const elapsed = state.clock.elapsedTime;
    meshRefs.current.forEach((mesh, index) => {
      if (!mesh) return;
      const base = positions[index];
      const drift = floatParams[index];
      if (!base || !drift) return;
      mesh.position.y = base[1] + Math.sin(elapsed * drift.speed + drift.phase) * drift.amplitude;
    });
  });

  return (
    <group ref={particlesRef}>
      {positions.map((pos, index) => (
        <mesh
          key={index}
          position={pos}
          ref={(mesh) => {
            meshRefs.current[index] = mesh;
          }}
        >
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
