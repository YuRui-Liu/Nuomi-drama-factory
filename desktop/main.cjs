const { app, BrowserWindow, dialog } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');
const { packagedPaths, backendEnvironment } = require('./runtime-paths.cjs');

let backendProcess;
let shuttingDown = false;

function reservePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      server.close(() => resolve(address.port));
    });
  });
}

async function waitForBackend(url, processHandle, timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (processHandle.exitCode !== null) throw new Error(`Backend exited with code ${processHandle.exitCode}`);
    try {
      const response = await fetch(`${url}/api/v1/config`);
      if (response.ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 350));
  }
  throw new Error(`Backend did not become ready within ${timeoutMs / 1000} seconds`);
}

function stopBackend() {
  if (!backendProcess || backendProcess.exitCode !== null) return;
  backendProcess.kill();
  backendProcess = undefined;
}

async function launch() {
  const paths = packagedPaths(process.resourcesPath);
  for (const required of [paths.python, paths.source, paths.frontend, paths.ffmpeg, paths.ffprobe]) {
    if (!fs.existsSync(required)) throw new Error(`Bundled runtime file is missing: ${required}`);
  }
  const port = await reservePort();
  const url = `http://127.0.0.1:${port}`;
  const environment = backendEnvironment(paths, app.getPath('userData'), port);
  fs.mkdirSync(environment.NOVELVIDEO_DATA_ROOT, { recursive: true });
  backendProcess = spawn(
    paths.python,
    ['-X', 'utf8', '-m', 'uvicorn', 'novelvideo.api.app:app', '--host', '127.0.0.1', '--port', String(port)],
    { env: environment, cwd: path.join(paths.runtime, 'backend'), windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] },
  );
  const logDir = path.join(app.getPath('userData'), 'logs');
  fs.mkdirSync(logDir, { recursive: true });
  const log = fs.createWriteStream(path.join(logDir, 'backend.log'), { flags: 'a' });
  backendProcess.stdout.pipe(log);
  backendProcess.stderr.pipe(log);
  await waitForBackend(url, backendProcess);

  const window = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1100,
    minHeight: 700,
    backgroundColor: '#111827',
    show: false,
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  window.once('ready-to-show', () => window.show());
  await window.loadURL(url);
}

app.whenReady().then(launch).catch((error) => {
  dialog.showErrorBox('DramaClaw failed to start', String(error.stack || error));
  app.quit();
});
app.on('window-all-closed', () => app.quit());
app.on('before-quit', () => {
  if (shuttingDown) return;
  shuttingDown = true;
  stopBackend();
});
