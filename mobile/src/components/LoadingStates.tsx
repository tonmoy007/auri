// Auri — Loading state UI helpers
// Shimmer text for in-progress states (transcribing, processing).

import React, { useEffect, useRef } from 'react';
import { Animated, StyleProp, TextStyle } from 'react-native';

interface ShimmerTextProps {
  children: string;
  style?: StyleProp<TextStyle>;
}

/**
 * Text that gently pulses opacity in a loop — used for "in progress"
 * status labels (e.g. "Anonymizing…") so the UI reads as alive rather
 * than stalled while a request is in flight.
 */
export function ShimmerText({ children, style }: ShimmerTextProps): React.JSX.Element {
  const opacity = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, {
          toValue: 0.4,
          duration: 700,
          useNativeDriver: true,
        }),
        Animated.timing(opacity, {
          toValue: 1,
          duration: 700,
          useNativeDriver: true,
        }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [opacity]);

  return <Animated.Text style={[style, { opacity }]}>{children}</Animated.Text>;
}
