const path = require('node:path');

function packagedPaths(resourcesPath) {
  const runtime = path.join(resourcesPath, 'runtime');
  return {
    runtime,
    python: path.join(runtime, 'python', 'python.exe'),
    source: path.join(runtime, 'backend', 'src'),
    frontend: path.join(runtime, 'frontend'),
    ffmpeg: path.join(runtime, 'ffmpeg', 'ffmpeg.exe'),
    ffprobe: path.join(runtime, 'ffmpeg', 'ffprobe.exe'),
  };
}

function backendEnvironment(paths, userDataPath, port, baseEnvironment = process.env) {
  const dataRoot = path.join(userDataPath, 'data');
  const pathValue = [path.dirname(paths.ffmpeg), baseEnvironment.PATH || '']
    .filter(Boolean)
    .join(path.delimiter);

  return {
    ...baseEnvironment,
    ST_EDITION: 'ce',
    ST_COOKIE_SECURE: '0',
    ST_CONTROL_PLANE_DSN: '',
    ST_REDIS_URL: '',
    ST_CELERY_BROKER_URL: '',
    ST_CELERY_RESULT_BACKEND: '',
    NOVELVIDEO_API_HOST: '127.0.0.1',
    NOVELVIDEO_API_PORT: String(port),
    NOVELVIDEO_DATA_ROOT: dataRoot,
    NOVELVIDEO_STATE_DIR: path.join(dataRoot, 'state'),
    NOVELVIDEO_RUNTIME_DIR: path.join(dataRoot, 'runtime'),
    NOVELVIDEO_OUTPUT_DIR: path.join(dataRoot, 'output'),
    DRAMACLAW_FRONTEND_DIST: paths.frontend,
    PYTHONPATH: paths.source,
    PYTHONUTF8: '1',
    PYTHONNOUSERSITE: '1',
    FFMPEG_PATH: paths.ffmpeg,
    FFPROBE_PATH: paths.ffprobe,
    PATH: pathValue,
  };
}

module.exports = { packagedPaths, backendEnvironment };
