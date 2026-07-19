/**
 * Detox config — critical-path E2E tests (record → transcribe → forward).
 *
 * UNVERIFIED: scaffolded 2026-07-20, never executed. This environment has
 * no Xcode/simulator or Android SDK to build and run against — the config
 * and starter test are written to the documented Detox+Expo pattern, but
 * confirming they actually build and pass requires a machine with a real
 * iOS simulator or Android emulator. Do not treat this as done until that
 * happens (see .hermes/plans task 6.4).
 */
module.exports = {
  testRunner: {
    args: {
      $0: 'jest',
      config: 'e2e/jest.config.js',
    },
    jest: {
      setupTimeout: 120000,
    },
  },
  apps: {
    'ios.debug': {
      type: 'ios.app',
      binaryPath: 'ios/build/Build/Products/Debug-iphonesimulator/Auri.app',
      build:
        'xcodebuild -workspace ios/Auri.xcworkspace -scheme Auri -configuration Debug -sdk iphonesimulator -derivedDataPath ios/build',
    },
    'android.debug': {
      type: 'android.apk',
      binaryPath: 'android/app/build/outputs/apk/debug/app-debug.apk',
      build:
        'cd android && ./gradlew assembleDebug assembleAndroidTest -DtestBuildType=debug',
    },
  },
  devices: {
    simulator: {
      type: 'ios.simulator',
      device: {
        type: 'iPhone 15',
      },
    },
    emulator: {
      type: 'android.emulator',
      device: {
        avdName: 'Pixel_7_API_34',
      },
    },
  },
  configurations: {
    'ios.sim.debug': {
      device: 'simulator',
      app: 'ios.debug',
    },
    'android.emu.debug': {
      device: 'emulator',
      app: 'android.debug',
    },
  },
};
