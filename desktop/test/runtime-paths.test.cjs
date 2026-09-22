const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const vm = require('node:vm');

const { packagedPaths, backendEnvironment } = require('../runtime-paths.cjs');

test('packaged paths resolve every bundled runtime component', () => {
  const resources = path.join('Program Files', 'DramaClaw', 'resources');
  const paths = packagedPaths(resources);
  assert.equal(paths.python, path.join(resources, 'runtime', 'python', 'python.exe'));
  assert.equal(paths.frontend, path.join(resources, 'runtime', 'frontend'));
  assert.equal(paths.source, path.join(resources, 'runtime', 'backend', 'src'));
  assert.equal(paths.ffmpeg, path.join(resources, 'runtime', 'ffmpeg', 'ffmpeg.exe'));
});

test('Windows runtime preserves drive paths, spaces, and semicolon PATH', () => {
  const sandbox = {
    module: { exports: {} },
    require(name) {
      assert.equal(name, 'node:path');
      return path.win32;
    },
  };
  vm.runInNewContext(fs.readFileSync(require.resolve('../runtime-paths.cjs'), 'utf8'), sandbox);
  const windows = sandbox.module.exports;
  const paths = windows.packagedPaths('C:\\Program Files\\DramaClaw\\resources');
  assert.equal(paths.python, 'C:\\Program Files\\DramaClaw\\resources\\runtime\\python\\python.exe');
  const env = windows.backendEnvironment(paths, 'D:\\User Data\\DramaClaw', 49152, { PATH: 'C:\\Windows' });
  assert.equal(env.NOVELVIDEO_DATA_ROOT, 'D:\\User Data\\DramaClaw\\data');
  assert.equal(env.PATH, 'C:\\Program Files\\DramaClaw\\resources\\runtime\\ffmpeg;C:\\Windows');
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
