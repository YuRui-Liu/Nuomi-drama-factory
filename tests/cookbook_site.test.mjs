import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const packageJson = JSON.parse(
  readFileSync(new URL('../package.json', import.meta.url), 'utf8'),
);
const gitignore = readFileSync(new URL('../.gitignore', import.meta.url), 'utf8');
const cookbookPackageJson = JSON.parse(
  readFileSync(new URL('../docs/cookbook/package.json', import.meta.url), 'utf8'),
);
const cookbookConfig = readFileSync(
  new URL('../docs/cookbook/.vitepress/config.mts', import.meta.url),
  'utf8',
);

test('root package scripts expose the cookbook site commands', () => {
  assert.equal(packageJson.scripts['docs:dev'], 'pnpm --dir docs/cookbook dev');
  assert.equal(packageJson.scripts['docs:build'], 'pnpm --dir docs/cookbook build');
  assert.equal(packageJson.scripts['docs:preview'], 'pnpm --dir docs/cookbook preview');
});

test('gitignore excludes VitePress build artifacts', () => {
  const rules = new Set(gitignore.split(/\r?\n/));

  assert.ok(rules.has('/docs/cookbook/.vitepress/dist/'));
  assert.ok(rules.has('/docs/cookbook/.vitepress/cache/'));
});

test('cookbook package exposes VitePress commands and Mermaid dependencies', () => {
  assert.deepEqual(cookbookPackageJson.scripts, {
    dev: 'vitepress .',
    build: 'vitepress build .',
    preview: 'vitepress preview .',
  });

  for (const dependency of ['vitepress', 'mermaid', 'vitepress-plugin-mermaid']) {
    assert.ok(
      cookbookPackageJson.devDependencies[dependency],
      `expected ${dependency} in devDependencies`,
    );
  }
});

test('cookbook navigation includes every site route', () => {
  const routes = [
    '/',
    '/start-software',
    '/system-map',
    '/development/trace-a-feature',
    '/development/add-api-and-task',
    '/development/storage-and-files',
    '/development/testing-strategy',
    '/pipelines/01-ingest',
    '/pipelines/02-episode-graph',
    '/pipelines/03-production-assets',
    '/pipelines/04-screenplay',
    '/pipelines/05-storyboard',
    '/pipelines/06-audio',
    '/pipelines/07-video',
    '/pipelines/08-compose-export',
  ];

  for (const route of routes) {
    assert.match(
      cookbookConfig,
      new RegExp(`link:\\s*['\"]${route.replaceAll('/', '\\/')}['\"]`),
      `expected navigation entry for ${route}`,
    );
  }
});

test('cookbook configuration enables local search and Mermaid', () => {
  assert.match(cookbookConfig, /withMermaid\s*\(/);
  assert.match(cookbookConfig, /provider:\s*['\"]local['\"]/);
});
