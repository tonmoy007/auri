/**
 * Starter E2E smoke test — navigation only.
 *
 * UNVERIFIED (see .detoxrc.js header): written but never run against a
 * real simulator in this environment.
 *
 * Deliberately scoped to navigation, not the full record → transcribe →
 * forward critical path from the plan: exercising real audio recording in
 * Detox needs either a granted-mic-permission simulator with an injected
 * audio fixture, or mocking expo-av's native recording module — neither
 * exists yet. This is the foundation to build that on, not the finished
 * suite.
 */
const { device, element, by, expect } = require('detox');

describe('Auri — app launch and navigation', () => {
  beforeAll(async () => {
    await device.launchApp();
  });

  beforeEach(async () => {
    await device.reloadReactNative();
  });

  it('shows the "Enter Auri" call-to-action on launch', async () => {
    await expect(
      element(by.label('Enter the confession booth')),
    ).toBeVisible();
  });

  it('opens Settings from the home screen and returns', async () => {
    await element(by.label('Open settings')).tap();
    await expect(element(by.text('Settings'))).toBeVisible();

    await element(by.label('Go back')).tap();
    await expect(
      element(by.label('Enter the confession booth')),
    ).toBeVisible();
  });

  it('enters the confession booth and can change environment', async () => {
    await element(by.label('Enter the confession booth')).tap();
    await expect(
      element(by.label('Change booth environment')),
    ).toBeVisible();
  });
});
