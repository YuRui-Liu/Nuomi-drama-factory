const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

function runtimeLayout(root) {
  return {
    root,
    python: path.join(root, 'python'),
    frontend: path.join(root, 'frontend'),
    backend: path.join(root, 'backend'),
    ffmpeg: path.join(root, 'ffmpeg'),
  };
}

function validateInputs(inputs) {
  return Object.entries(inputs)
    .filter(([, inputPath]) => !fs.existsSync(inputPath))
    .map(([name, inputPath]) => `${name}: ${inputPath}`);
}

function copyTree(source, destination, filter = () => true) {
  fs.cpSync(source, destination, { recursive: true, force: true, filter });
}

function findManagedPython(projectRoot) {
  const installRoot = path.join(projectRoot, '.python');
  if (!fs.existsSync(installRoot)) return '';
  const directory = fs.readdirSync(installRoot, { withFileTypes: true })
    .find((entry) => entry.isDirectory() && entry.name.startsWith('cpython-3.11'));
  return directory ? path.join(installRoot, directory.name) : '';
}

function sitePackagesPath(projectRoot) {
  return path.join(projectRoot, '.venv', 'Lib', 'site-packages');
}

function stageRuntime(projectRoot = path.resolve(__dirname, '..', '..')) {
  const outputRoot = path.join(projectRoot, 'desktop-runtime');
  const layout = runtimeLayout(outputRoot);
  const pythonRoot = findManagedPython(projectRoot);
  const sitePackages = sitePackagesPath(projectRoot);
  const frontendDist = path.join(projectRoot, 'frontend', 'dist');
  const source = path.join(projectRoot, 'src');
  const ffmpegPackage = path.join(projectRoot, 'node_modules', '@ffmpeg-installer', 'win32-x64');
  const ffprobePackage = path.join(projectRoot, 'node_modules', 'ffprobe-static');
  const inputs = {
    pythonRoot,
    sitePackages,
    frontendDist,
    source,
    ffmpeg: path.join(ffmpegPackage, 'ffmpeg.exe'),
    ffprobe: path.join(ffprobePackage, 'bin', 'win32', 'x64', 'ffprobe.exe'),
  };
  const missing = validateInputs(inputs);
  if (missing.length) {
    throw new Error(`Missing desktop build prerequisites:\n${missing.join('\n')}`);
  }

  fs.rmSync(outputRoot, { recursive: true, force: true });
  fs.mkdirSync(outputRoot, { recursive: true });
  const exclude = (sourcePath) => !/[\\/](?:__pycache__|tests?|test_data|\.pytest_cache)(?:[\\/]|$)/i.test(sourcePath);
  copyTree(pythonRoot, layout.python, exclude);
  copyTree(sitePackages, path.join(layout.python, 'Lib', 'site-packages'), exclude);
  copyTree(frontendDist, layout.frontend);
  copyTree(source, path.join(layout.backend, 'src'), exclude);
  fs.mkdirSync(layout.ffmpeg, { recursive: true });
  fs.copyFileSync(inputs.ffmpeg, path.join(layout.ffmpeg, 'ffmpeg.exe'));
  fs.copyFileSync(inputs.ffprobe, path.join(layout.ffmpeg, 'ffprobe.exe'));
  for (const [sourcePath, outputName] of [
    [path.join(ffmpegPackage, 'README.md'), 'ffmpeg-README.md'],
    [path.join(ffprobePackage, 'LICENSE'), 'ffprobe-LICENSE.txt'],
  ]) {
    if (fs.existsSync(sourcePath)) fs.copyFileSync(sourcePath, path.join(layout.ffmpeg, outputName));
  }
  fs.copyFileSync(
    path.join(projectRoot, 'desktop', 'assets', 'ffmpeg-NOTICE.txt'),
    path.join(layout.ffmpeg, 'NOTICE.txt'),
  );

  const verification = spawnSync(
    path.join(layout.python, 'python.exe'),
    [
      '-X',
      'utf8',
      '-c',
      'import novelvideo, transformers, uvicorn; print(novelvideo.__file__)',
    ],
    {
      env: {
        ...process.env,
        PYTHONPATH: path.join(layout.backend, 'src'),
        PYTHONNOUSERSITE: '1',
        PYTHONUTF8: '1',
      },
      encoding: 'utf8',
    },
  );
  if (verification.status !== 0) {
    throw new Error(`Staged Python verification failed:\n${verification.stderr || verification.stdout}`);
  }
  return layout;
}

if (require.main === module) {
  const layout = stageRuntime();
  console.log(`Desktop runtime staged at ${layout.root}`);
}

module.exports = { runtimeLayout, validateInputs, findManagedPython, sitePackagesPath, stageRuntime };
