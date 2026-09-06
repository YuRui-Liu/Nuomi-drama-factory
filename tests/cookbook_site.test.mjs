import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const packageJson = JSON.parse(
  readFileSync(new URL('../package.json', import.meta.url), 'utf8'),
);
const gitignore = readFileSync(new URL('../.gitignore', import.meta.url), 'utf8');

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
