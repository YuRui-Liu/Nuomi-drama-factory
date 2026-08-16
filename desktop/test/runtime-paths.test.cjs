const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const { packagedPaths, backendEnvironment } = require('../runtime-paths.cjs');

test('packaged paths resolve every bundled runtime component', () => {
  const resources = path.win32.join('C:\\Program Files', 'DramaClaw', 'resources');
  const paths = packagedPaths(resources);
  assert.equal(paths.python, path.win32.join(resources, 'runtime', 'python', 'python.exe'));
  assert.equal(paths.frontend, path.win32.join(resources, 'runtime', 'frontend'));
  assert.equal(paths.source, path.win32.join(resources, 'runtime', 'backend', 'src'));
  assert.equal(paths.ffmpeg, path.win32.join(resources, 'runtime', 'ffmpeg', 'ffmpeg.exe'));
});

test('backend environment enables standalone CE and persistent user data', () => {
  const paths = packagedPaths('C:\\DramaClaw\\resources');
  const env = backendEnvironment(paths, 'C:\\Users\\Frank\\AppData\\Roaming\\DramaClaw', 49152, {});
  assert.equal(env.ST_EDITION, 'ce');
  assert.equal(env.ST_COOKIE_SECURE, '0');
  assert.equal(env.NOVELVIDEO_API_PORT, '49152');
  assert.equal(env.PYTHONPATH, paths.source);
  assert.equal(env.FFMPEG_PATH, paths.ffmpeg);
  assert.equal(env.DRAMACLAW_FRONTEND_DIST, paths.frontend);
  assert.match(env.NOVELVIDEO_DATA_ROOT, /DramaClaw[\\/]data$/);
});
