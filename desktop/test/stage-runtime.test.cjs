const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const { runtimeLayout, validateInputs, sitePackagesPath } = require('../scripts/stage-runtime.cjs');

test('runtime layout has stable installer resource paths', () => {
  const layout = runtimeLayout(path.resolve('desktop-runtime'));
  assert.match(layout.python, /desktop-runtime[\\/]python$/);
  assert.match(layout.frontend, /desktop-runtime[\\/]frontend$/);
  assert.match(layout.backend, /desktop-runtime[\\/]backend$/);
  assert.match(layout.ffmpeg, /desktop-runtime[\\/]ffmpeg$/);
});

test('desktop staging uses the canonical dot-venv', () => {
  assert.equal(
    sitePackagesPath('E:\\project'),
    path.join('E:\\project', '.venv', 'Lib', 'site-packages'),
  );
});

test('input validation reports all missing build prerequisites', () => {
  const missing = validateInputs({
    pythonRoot: 'Z:\\missing-python',
    sitePackages: 'Z:\\missing-site-packages',
    frontendDist: 'Z:\\missing-frontend',
    source: 'Z:\\missing-source',
    ffmpeg: 'Z:\\missing-ffmpeg.exe',
    ffprobe: 'Z:\\missing-ffprobe.exe',
  });
  assert.equal(missing.length, 6);
});
