module.exports = {
  ...require('../package.json').build,
  appId: 'ai.nuomidrama.desktop',
  productName: 'NuomiDrama',
  directories: { output: 'dist-desktop' },
  mac: {
    target: [{ target: 'dmg', arch: ['arm64'] }],
    artifactName: 'NuomiDrama-${version}-mac-${arch}.${ext}',
    category: 'public.app-category.video',
    minimumSystemVersion: '14.0',
    hardenedRuntime: true,
    entitlements: 'desktop/assets/entitlements.mac.plist',
    entitlementsInherit: 'desktop/assets/entitlements.mac.plist',
    notarize: process.env.NUOMI_NOTARIZE === '1',
    extendInfo: {
      NSMicrophoneUsageDescription: '用于录制角色声音，录音仅在您主动操作时开始。',
    },
  },
  dmg: { title: 'NuomiDrama' },
};
