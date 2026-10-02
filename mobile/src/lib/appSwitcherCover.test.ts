import { describe, expect, it } from 'vitest';
import { shouldCoverScreen } from './appSwitcherCover';

describe('shouldCoverScreen', () => {
  it('shows the conversation only while the app is active', () => {
    expect(shouldCoverScreen('active')).toBe(false);
  });

  it('covers it as the app leaves the foreground', () => {
    // iOS reports inactive while the app switcher is open, before the snapshot
    expect(shouldCoverScreen('inactive')).toBe(true);
    expect(shouldCoverScreen('background')).toBe(true);
  });

  it('covers it when the state is not known', () => {
    expect(shouldCoverScreen('unknown')).toBe(true);
    expect(shouldCoverScreen(null)).toBe(true);
    expect(shouldCoverScreen(undefined)).toBe(true);
  });
});
