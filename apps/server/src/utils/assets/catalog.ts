import { createHash } from "node:crypto";
import { dirname, extname, join } from "node:path";
import { lstat, mkdir, readFile, realpath, writeAtomic } from "@toonflow/file";
import { assetCatalogCommandSchema, assetCatalogSchema, type AssetCatalog } from "@toonflow/node-shared/production/assetCatalog";
import conf from "@/utils/conf";
import { lockWorkspaceFiles, resolveWorkspacePath } from "@/utils/workspace/files";
import { getAssetsDirectory } from "@/utils/assets";

const catalogPath = () => join(dirname(conf.path), "assetCatalog.json");
function fail(message: string, status = 409): never { throw Object.assign(new Error(message), { status }); }

export async function readCatalog(): Promise<AssetCatalog> {
  try { return assetCatalogSchema.parse(JSON.parse(await readFile(catalogPath(), "utf8"))); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return { revision: 0, works: [], assets: [], uses: [] };
    throw error;
  }
}
export async function executeCatalog(directory: string, input: unknown) {
  const command = assetCatalogCommandSchema.parse(input);
  const root = await realpath(directory);
  const release = lockWorkspaceFiles([catalogPath()]);
  try {
    const catalog = await readCatalog();
    if (catalog.revision !== command.expectedRevision) fail("资产库已变化，请重读后保存");
    const work = (workId: string) => {
      const item = catalog.works.find(item => item.id === workId);
      if (!item || item.directory !== root) fail("作品不属于当前工作目录", 400);
      return item;
    };
    const findVersion = (ref: { assetId: string; version: number }) => {
      const asset = catalog.assets.find(item => item.id === ref.assetId);
      const version = asset?.versions.find(item => item.version === ref.version);
      if (!asset || !version) fail("资产版本不存在", 404);
      return { asset, version };
    };
    let result: unknown;
    if (command.action === "saveWork") {
      const existing = command.workId ? work(command.workId) : catalog.works.find(item => item.directory === root);
      if (existing) { existing.title = command.title; result = existing; }
      else {
        const item = { id: crypto.randomUUID().replaceAll("-", ""), title: command.title, directory: root };
        catalog.works.push(item); result = item;
      }
    } else if (command.action === "saveVersion") {
      work(command.workId);
      const existing = command.assetId ? catalog.assets.find(item => item.id === command.assetId) : undefined;
      if (command.assetId && !existing) fail("资产不存在", 404);
      if (existing && (existing.ownerWorkId !== command.workId || existing.kind !== command.kind)) fail("不能改变资产归属或类型；独立修改请新建派生资产", 400);
      const parentRelations = command.relations.filter(item => item.relation === "variantOf");
      const variant = command.kind === "characterVariant" || command.kind === "sceneVariant";
      if (parentRelations.length !== Number(variant)) fail("变体必须且只能关联一个基础资产；基础资产不能使用 variantOf", 400);
      for (const relation of command.relations) {
        if (relation.target.assetId === command.assetId) fail("资产关系不能引用自身", 400);
        const target = findVersion(relation.target);
        if (!target.version.approval) fail("资产关系必须引用已采用版本");
        if (relation.relation === "variantOf" && target.asset.kind !== (command.kind === "characterVariant" ? "character" : "scene")) fail("变体与基础资产类型不匹配", 400);
        if (relation.relation === "voiceOf" && (command.kind !== "voice" || !["character", "characterVariant"].includes(target.asset.kind))) fail("音色只能绑定人物资产", 400);
      }
      const source = await resolveWorkspacePath(root, command.sourcePath);
      const info = await lstat(source.path);
      if (!info.isFile() || info.size > 100 * 1024 * 1024) fail("资产必须是 100 MB 以内的普通媒体文件", 400);
      const extension = extname(source.path).toLowerCase();
      if (!(command.kind === "voice" ? [".mp3", ".wav", ".flac", ".ogg", ".m4a"] : [".png", ".jpg", ".jpeg", ".webp"]).includes(extension)) fail("资产媒体类型与资产类别不符", 400);
      const bytes = await readFile(source.path);
      if (bytes.byteLength > 100 * 1024 * 1024) fail("资产文件过大", 400);
      const assetId = existing?.id ?? crypto.randomUUID().replaceAll("-", "");
      const version = (existing?.versions.at(-1)?.version ?? 0) + 1;
      const path = `managed/${assetId}/v${version}${crypto.randomUUID().replaceAll("-", "")}${extension}`;
      const assetRoot = await getAssetsDirectory();
      const target = await resolveWorkspacePath(assetRoot, path, true);
      await mkdir(dirname(target.path), { recursive: true });
      const checked = await resolveWorkspacePath(assetRoot, path);
      await writeAtomic(checked.path, bytes, { exclusive: true });
      const value = { version, name: command.name, reason: command.reason, path, sourcePath: command.sourcePath, hash: createHash("sha256").update(bytes).digest("hex"), relations: command.relations, createdAt: new Date().toISOString() };
      if (existing) existing.versions.push(value);
      else catalog.assets.push({ id: assetId, ownerWorkId: command.workId, kind: command.kind, versions: [value] });
      result = { assetId, version };
    } else {
      const target = findVersion(command.ref);
      if (command.action === "adoptVersion") {
        work(target.asset.ownerWorkId);
        if (target.version.approval) fail("该版本已采用，不重复覆盖确认依据");
        target.version.approval = command.approval;
      } else {
        work(command.workId);
        if (!target.version.approval) fail("只能复用已采用的资产版本");
        catalog.uses = catalog.uses.filter(item => item.workId !== command.workId || item.ref.assetId !== command.ref.assetId);
        catalog.uses.push({ workId: command.workId, ref: command.ref, approval: command.approval });
      }
      result = command.ref;
    }
    catalog.revision++;
    await writeAtomic(catalogPath(), JSON.stringify(assetCatalogSchema.parse(catalog), null, 2));
    return { revision: catalog.revision, result };
  } finally { release(); }
}
