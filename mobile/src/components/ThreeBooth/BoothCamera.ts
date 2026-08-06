// Auri — booth camera rig: ambient idle drift + recording-driven dolly

import { useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { MathUtils } from 'three';

/** Base distance from the booth along Z, matching ThreeCanvas's initial camera position. */
const BASE_DISTANCE_Z = 5;
/** How far the camera dollies in toward the candle while recording, in world units. */
const RECORD_DOLLY_IN = 0.8;
/** How quickly the dolly chases its target — higher = snappier transition. */
const DOLLY_SMOOTHING_SPEED = 1.5;

interface BoothCameraProps {
  /** Dollies the camera in while true, eases back out to the base distance otherwise. */
  isRecording?: boolean;
}

/**
 * Booth camera rig.
 * Drifts gently side to side at idle for atmosphere, pulls in toward the
 * candle when recording starts, and eases back out when it ends.
 */
export function BoothCamera({ isRecording = false }: BoothCameraProps): null {
  const dolly = useRef(0);

  useFrame((state, delta) => {
    const targetDolly = isRecording ? RECORD_DOLLY_IN : 0;
    dolly.current = MathUtils.lerp(
      dolly.current,
      targetDolly,
      Math.min(delta * DOLLY_SMOOTHING_SPEED, 1),
    );

    const t = state.clock.elapsedTime;
    state.camera.position.x = Math.sin(t * 0.05) * 0.3;
    state.camera.position.y = Math.sin(t * 0.03) * 0.1 + 0.5;
    state.camera.position.z = BASE_DISTANCE_Z - dolly.current;
    state.camera.lookAt(0, 0, 0);
  });

  return null;
}
