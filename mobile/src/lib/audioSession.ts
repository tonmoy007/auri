// Auri — Audio session configuration shared by recording and playback

import { Audio, InterruptionModeAndroid, InterruptionModeIOS } from 'expo-av';

/**
 * Audio session modes.
 *
 * expo-av's audio mode is process-global, so whichever screen sets it last
 * wins for the whole app. Recording needs `allowsRecordingIOS: true`, which
 * puts iOS into the PlayAndRecord category — and leaving it there routes
 * later playback to the earpiece at low volume, or fails it outright. The
 * booth used to set the recording mode once at mount and never restore it,
 * so the review screen inherited a session still configured to record.
 *
 * Keeping both modes here means the two callers can't drift apart.
 */

/** Configure the session for capturing microphone input. */
export async function configureForRecording(): Promise<void> {
  await Audio.setAudioModeAsync({
    allowsRecordingIOS: true,
    playsInSilentModeIOS: true,
    staysActiveInBackground: false,
    interruptionModeIOS: InterruptionModeIOS.DuckOthers,
    interruptionModeAndroid: InterruptionModeAndroid.DuckOthers,
    shouldDuckAndroid: true,
    playThroughEarpieceAndroid: false,
  });
}

/**
 * Configure the session for playing audio back.
 *
 * `allowsRecordingIOS: false` is the point of this call: it moves iOS out
 * of PlayAndRecord so playback goes to the speaker at full volume.
 * `playThroughEarpieceAndroid: false` keeps Android on the speaker too — a
 * confession played back through the earpiece would be inaudible in the
 * way a user would read as "it didn't play".
 */
export async function configureForPlayback(): Promise<void> {
  await Audio.setAudioModeAsync({
    allowsRecordingIOS: false,
    playsInSilentModeIOS: true,
    staysActiveInBackground: false,
    interruptionModeIOS: InterruptionModeIOS.DuckOthers,
    interruptionModeAndroid: InterruptionModeAndroid.DuckOthers,
    shouldDuckAndroid: true,
    playThroughEarpieceAndroid: false,
  });
}
