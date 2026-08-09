// Auri — Per-environment 3D backdrop dressing
// Classic/forest/rooftop previously only differed by background/light/particle
// tint (see ConfessionBooth.tsx, ThreeCanvas.tsx) — this adds distinct
// atmospheric geometry per preset so each environment actually reads as a
// different place, not just a recolored booth.
//
// Only meshBasicMaterial/meshStandardMaterial + primitive geometries are used
// here, matching the rest of the scene — drei's shader-based helpers (e.g.
// Stars) are avoided because expo-gl's WebGL shim has already needed manual
// patching for custom-shader edge cases (see ThreeCanvas.tsx), and there's no
// simulator in this environment to verify a new shader against it.

import React, { useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { Mesh } from 'three';
import type { Environment } from '../../types';

interface EnvironmentBackdropProps {
  environment: Environment;
}

/** Renders the extra scenery for the active environment, if any. */
export function EnvironmentBackdrop({
  environment,
}: EnvironmentBackdropProps): React.JSX.Element | null {
  switch (environment) {
    case 'forest':
      return (
        <>
          <ForestCanopy />
          <Fireflies count={14} />
        </>
      );
    case 'rooftop':
      return (
        <>
          <NightSky count={80} />
          <Moon />
          <Skyline />
        </>
      );
    case 'classic':
    default:
      return null;
  }
}

/** Ring of dark cone "trees" around the booth, framing it as a forest glade. */
function ForestCanopy(): React.JSX.Element {
  const trees = useMemo(() => {
    const count = 10;
    return Array.from({ length: count }, (_, i) => {
      const angle = (i / count) * Math.PI * 2;
      const radius = 4.5 + Math.random() * 1.5;
      const height = 2.5 + Math.random() * 1.5;
      return {
        position: [Math.cos(angle) * radius, -1.5, Math.sin(angle) * radius - 1] as [
          number,
          number,
          number,
        ],
        height,
      };
    });
  }, []);

  return (
    <group>
      {trees.map((tree, index) => (
        <mesh key={index} position={tree.position}>
          <coneGeometry args={[0.6, tree.height, 8]} />
          <meshStandardMaterial color="#0f2e14" roughness={0.9} />
        </mesh>
      ))}
    </group>
  );
}

/** Slow-drifting glowing motes, denser and greener than the booth's ambient dust. */
function Fireflies({ count }: { count: number }): React.JSX.Element {
  const meshRefs = useRef<(Mesh | null)[]>([]);

  const flies = useMemo(
    () =>
      Array.from({ length: count }, () => ({
        base: [
          (Math.random() - 0.5) * 5,
          (Math.random() - 0.5) * 2 + 0.5,
          (Math.random() - 0.5) * 4 - 1,
        ] as [number, number, number],
        speed: 0.2 + Math.random() * 0.3,
        phase: Math.random() * Math.PI * 2,
        radius: 0.3 + Math.random() * 0.4,
      })),
    [count],
  );

  useFrame((state) => {
    const elapsed = state.clock.elapsedTime;
    meshRefs.current.forEach((mesh, index) => {
      const fly = flies[index];
      if (!mesh || !fly) return;
      const [bx, by, bz] = fly.base;
      mesh.position.x = bx + Math.cos(elapsed * fly.speed + fly.phase) * fly.radius;
      mesh.position.y = by + Math.sin(elapsed * fly.speed * 1.4 + fly.phase) * fly.radius;
      mesh.position.z = bz;
    });
  });

  return (
    <group>
      {flies.map((fly, index) => (
        <mesh
          key={index}
          position={fly.base}
          ref={(mesh) => {
            meshRefs.current[index] = mesh;
          }}
        >
          <sphereGeometry args={[0.03, 6, 6]} />
          <meshBasicMaterial color="#bef264" transparent opacity={0.8} />
        </mesh>
      ))}
    </group>
  );
}

/** Static scattered points standing in for a night sky, far behind the booth. */
function NightSky({ count }: { count: number }): React.JSX.Element {
  const stars = useMemo(
    () =>
      Array.from({ length: count }, () => ({
        position: [
          (Math.random() - 0.5) * 20,
          Math.random() * 8 + 1,
          -8 - Math.random() * 6,
        ] as [number, number, number],
        size: 0.015 + Math.random() * 0.02,
      })),
    [count],
  );

  return (
    <group>
      {stars.map((star, index) => (
        <mesh key={index} position={star.position}>
          <sphereGeometry args={[star.size, 4, 4]} />
          <meshBasicMaterial color="#e2e8f0" transparent opacity={0.6 + Math.random() * 0.4} />
        </mesh>
      ))}
    </group>
  );
}

/** Soft glowing moon, layered spheres for a cheap bloom-like halo. */
function Moon(): React.JSX.Element {
  return (
    <group position={[2.5, 3, -7]}>
      <mesh>
        <sphereGeometry args={[0.4, 16, 16]} />
        <meshBasicMaterial color="#f1f5f9" />
      </mesh>
      <mesh>
        <sphereGeometry args={[0.55, 16, 16]} />
        <meshBasicMaterial color="#f1f5f9" transparent opacity={0.15} />
      </mesh>
    </group>
  );
}

/** Rooftop horizon — dark building silhouettes of varying height. */
function Skyline(): React.JSX.Element {
  const buildings = useMemo(() => {
    const count = 9;
    return Array.from({ length: count }, (_, i) => {
      const x = (i - count / 2) * 1.3 + (Math.random() - 0.5) * 0.4;
      const height = 1 + Math.random() * 2.5;
      const width = 0.6 + Math.random() * 0.5;
      return {
        position: [x, -1.5 + height / 2, -6] as [number, number, number],
        height,
        width,
      };
    });
  }, []);

  return (
    <group>
      {buildings.map((building, index) => (
        <mesh key={index} position={building.position}>
          <boxGeometry args={[building.width, building.height, building.width]} />
          <meshStandardMaterial color="#0f172a" roughness={0.8} />
        </mesh>
      ))}
    </group>
  );
}
