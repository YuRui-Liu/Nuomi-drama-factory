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

test('cookbook configuration keeps the approved Chinese information architecture', () => {
  assert.match(cookbookConfig, /defineConfig\s*\(/);
  assert.match(cookbookConfig, /lang:\s*['\"]zh-CN['\"]/);
  assert.match(cookbookConfig, /ignoreDeadLinks:\s*false/);

  for (const label of [
    'Cookbook 首页',
    '系统地图',
    '启动与调试',
    '中文文档',
    '入门',
    '开发维护',
    '生产管线',
  ]) {
    assert.match(cookbookConfig, new RegExp("text:\\s*['\"]" + label + "['\"]"));
  }

  assert.match(
    cookbookConfig,
    /https:\/\/github\.com\/YuRui-Liu\/Nuomi-drama-factory\/blob\/main\/docs\/zh\/README\.md/,
  );
  assert.match(cookbookConfig, /level:\s*\[2,\s*3\]/);
  assert.match(cookbookConfig, /label:\s*['\"]本页目录['\"]/);
  assert.match(cookbookConfig, /prev:\s*['\"]上一篇['\"]/);
  assert.match(cookbookConfig, /next:\s*['\"]下一篇['\"]/);
});

test('cookbook configuration defines light Mermaid styling and dark-mode support', () => {
  assert.match(cookbookConfig, /withMermaid\s*\(/);
  assert.match(cookbookConfig, /mermaid:\s*{[\s\S]*?theme:\s*['\"]default['\"]/);
  assert.ok(cookbookPackageJson.devDependencies['vitepress-plugin-mermaid']);
});

test('cookbook theme extends the VitePress default theme', () => {
  const theme = readFileSync(
    new URL('../docs/cookbook/.vitepress/theme/index.ts', import.meta.url),
    'utf8',
  );
  const css = readFileSync(
    new URL('../docs/cookbook/.vitepress/theme/custom.css', import.meta.url),
    'utf8',
  );

  assert.match(theme, /from\s+['\"]vitepress\/theme['\"]/);
  assert.match(theme, /import\s+['\"]\.\/custom\.css['\"]/);
  assert.match(theme, /export\s+default\s+DefaultTheme/);
  assert.match(css, /--vp-font-family-base/);
  assert.match(css, /--vp-layout-max-width/);
  assert.match(css, /\.vp-doc h2/);
  assert.match(css, /\.vp-doc h3/);
  assert.match(css, /\.vp-doc :not\(pre\) > code/);
  assert.match(css, /\.vp-doc table/);
  assert.match(css, /overflow-x:\s*auto/);
  assert.match(css, /\.vp-doc details/);
  assert.match(css, /\.vp-doc \.mermaid/);
  assert.match(
    css,
    /@media \(max-width: 768px\)[\s\S]*?\.VPDoc \.content-container[\s\S]*?max-width:\s*688px !important/,
  );
  assert.doesNotMatch(css, /--vp-c-brand-/);
});

test('cookbook home documents the HTML reading commands', () => {
  const home = readFileSync(
    new URL('../docs/cookbook/README.md', import.meta.url),
    'utf8',
  );

  for (const command of ['pnpm docs:dev', 'pnpm docs:build', 'pnpm docs:preview']) {
    assert.match(home, new RegExp(command.replace(':', '\\:')));
  }
  assert.match(home, /pnpm --dir docs\/cookbook install/);
  assert.match(home, /HTML 阅读入口/);
  assert.match(home, /仓库根目录/);
  assert.match(home, /Markdown 仍是唯一内容源/);
});
