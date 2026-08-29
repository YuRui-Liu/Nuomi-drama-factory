import { readFileSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const FREEZONE_ENTRY_SOURCE =
  "src/routes/_app/projects.$project/freezone.lazy.tsx";
export const FREEZONE_MAX_BYTES = 2_020_000;

export function findFreezoneEntry(manifest) {
  const entries = Object.values(manifest);
  const entry =
    entries.find((item) => item.src === FREEZONE_ENTRY_SOURCE) ??
    entries.find(
      (item) => item.name === "freezone.lazy" && item.isDynamicEntry === true,
    );
  if (!entry) {
    throw new Error(
      `Freezone manifest entry not found: ${FREEZONE_ENTRY_SOURCE}`,
    );
  }
  return entry;
}

export function assertFreezoneBudget(bytes) {
  if (bytes > FREEZONE_MAX_BYTES) {
    throw new Error(
      `Freezone entry ${bytes} bytes exceeds ${FREEZONE_MAX_BYTES} byte budget`,
    );
  }
}

function logDependencies(kind, keys, manifest, distDir) {
  for (const key of keys ?? []) {
    const file = manifest[key]?.file;
    if (typeof file !== "string") continue;
    const bytes = statSync(resolve(distDir, file)).size;
    console.log(`[bundle-budget] ${kind} ${file}: ${bytes} bytes`);
  }
}

export function checkFreezoneBundle(rootDir) {
  const distDir = resolve(rootDir, "dist");
  const manifest = JSON.parse(
    readFileSync(resolve(distDir, ".vite/manifest.json"), "utf8"),
  );
  const entry = findFreezoneEntry(manifest);
  const bytes = statSync(resolve(distDir, entry.file)).size;

  console.log(`[bundle-budget] freezone ${entry.file}: ${bytes} bytes`);
  logDependencies("direct import", entry.imports, manifest, distDir);
  logDependencies("dynamic import", entry.dynamicImports, manifest, distDir);
  assertFreezoneBudget(bytes);

  return { bytes, entry };
}

const scriptPath = fileURLToPath(import.meta.url);
if (process.argv[1] && resolve(process.argv[1]) === scriptPath) {
  checkFreezoneBundle(resolve(dirname(scriptPath), ".."));
}