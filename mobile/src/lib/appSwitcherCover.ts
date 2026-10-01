// Auri — hiding the Guide conversation from the app-switcher thumbnail (plan task 16.6)
//
// The OS takes a picture of the screen for the app switcher as the app leaves the
// foreground, so a question typed there would sit in plain view among the recent
// apps. The screen draws a cover over the conversation whenever the app is not
// active. On iOS the switcher first reports `inactive`, in time for the cover to be
// drawn before the picture. On Android there is no `inactive` and the picture can
// be taken before JavaScript runs, so the screen also sets FLAG_SECURE through
// expo-screen-capture, which blanks the picture natively.

/** The states React Native's AppState reports. */
export type AppStateName = 'active' | 'background' | 'inactive' | 'unknown' | 'extension';

/** Cover the screen in any state but `active`. */
export function shouldCoverScreen(state: AppStateName | string | null | undefined): boolean {
  return state !== 'active';
}
