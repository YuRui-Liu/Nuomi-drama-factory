import { mkdir, readFile, realpath, writeAtomic } from "@toonflow/file";
import { dirname, resolve } from "node:path";
import { productionManifestSchema, productionRecordSchema } from "@toonflow/node-shared/production/schemas";
import type { ProductionManifest, ProductionRecord, ProductionRef } from "@toonflow/node-shared/production/types";
import { lockWorkspaceFiles, resolveWorkspacePath } from "@/utils/workspace/files";

export function failure(message: string, status = 409): never {
  throw Object.assign(new Error(message), { status });
}

export async function readManifest(directory: string): Promise<ProductionManifest> {
  const { path } = await resolveWorkspacePath(directory, "production/manifest.json", true);
  try { return productionManifestSchema.parse(JSON.parse(await readFile(path, "utf8"))); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return { version: 1, objects: [], adoptions: [] };
    throw error;
  }
}

export async function readRecord(directory: string, manifest: ProductionManifest, ref: ProductionRef): Promise<ProductionRecord> {
  const entry = manifest.objects.find(item => item.id === ref.id)?.revisions.find(item => item.revision === ref.revision);
  if (!entry) failure("制作成果或修订不存在", 404);
  if (!entry.path.startsWith(`production/objects/${ref.id}/`)) failure("制作索引路径无效", 400);
  const { path } = await resolveWorkspacePath(directory, entry.path);
  const record = productionRecordSchema.parse(JSON.parse(await readFile(path, "utf8")));
  if (record.id !== ref.id || record.revision !== ref.revision) failure("制作成果与索引不匹配", 400);
  return record;
}

export async function saveRecord(directory: string, record: ProductionRecord) {
  // ACT: 修订文件使用独立名称；索引写入失败留下未引用文件，重试不覆盖旧文件。
  const relative = `production/objects/${record.id}/revision${record.revision}v${crypto.randomUUID().replaceAll("-", "")}.json`;
  const target = await resolveWorkspacePath(directory, relative, true);
  await mkdir(dirname(target.path), { recursive: true });
  const checked = await resolveWorkspacePath(directory, relative);
  await writeAtomic(checked.path, JSON.stringify(record, null, 2), { exclusive: true });
  return relative;
}

export async function updateManifest<T>(directory: string, operation: (manifest: ProductionManifest, root: string) => Promise<T>) {
  const root = await realpath(directory);
  const release = lockWorkspaceFiles([resolve(root, "production")]);
  try {
    const manifest = await readManifest(root);
    const result = await operation(manifest, root);
    const target = await resolveWorkspacePath(root, "production/manifest.json", true);
    await mkdir(dirname(target.path), { recursive: true });
    const checked = await resolveWorkspacePath(root, "production/manifest.json");
    await writeAtomic(checked.path, JSON.stringify(productionManifestSchema.parse(manifest), null, 2));
    return result;
  } finally { release(); }
}
