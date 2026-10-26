import { lstat, realpath } from "@toonflow/file";
import { productionExecuteSchema, productionReadSchema } from "@toonflow/node-shared/production/operations";
import type { ProductionContent, ProductionManifest, ProductionRecord, ProductionRef } from "@toonflow/node-shared/production/types";
import { resolveWorkspacePath } from "@/utils/workspace/files";
import { failure, readManifest, readRecord, saveRecord, updateManifest } from "./store";

function sameRef(left: ProductionRef | null | undefined, right: ProductionRef | null | undefined) {
  return left?.id === right?.id && left?.revision === right?.revision;
}

function contentReferences(content: ProductionContent): ProductionRef[] {
  switch (content.kind) {
    case "episode": return [content.series];
    case "dialogue": return [content.voice];
    case "clip": return [content.plan];
    case "film": return [...content.entries.map(entry => entry.clip), ...content.dialogue];
    case "acceptance": return [content.film];
    case "h3Plan": return content.segments.flatMap(segment => segment.references.map(reference => reference.asset));
    default: return [];
  }
}

function mediaPaths(content: ProductionContent) {
  const paths = "media" in content ? [content.media.path] : [];
  if (content.kind === "film") paths.push(content.subtitlesPath, ...content.audioPaths);
  if (content.kind === "acceptance") paths.push(...content.checks.flatMap(check => check.evidencePaths));
  return paths;
}

async function validateContent(directory: string, manifest: ProductionManifest, content: ProductionContent) {
  const unique = (values: (string | number)[], label: string) => {
    if (new Set(values).size !== values.length) failure(`${label}不能重复`, 400);
  };
  if (content.kind === "series") {
    unique(content.characters.map(item => item.id), "角色 ID");
    unique(content.chapters.map(item => item.id), "篇章 ID");
    unique(content.episodes.map(item => item.id), "分集 ID");
    unique(content.episodes.map(item => item.number), "集数");
    if (content.episodes.some(item => item.chapterId && !content.chapters.some(chapter => chapter.id === item.chapterId))) failure("分集引用的篇章不存在", 400);
  }
  if (content.kind === "script") {
    unique(content.scenes.map(item => item.id), "场景 ID");
    unique(content.scenes.flatMap(scene => scene.shots.map(shot => shot.id)), "镜头 ID");
    unique(content.scenes.flatMap(scene => scene.shots.flatMap(shot => shot.dialogue.map(line => line.id))), "对白 ID");
  }
  if (content.kind === "h3Plan") unique(content.segments.map(item => item.id), "生成段 ID");
  for (const path of mediaPaths(content)) {
    const target = await resolveWorkspacePath(directory, path);
    if (!(await lstat(target.path)).isFile()) failure("成果引用必须是实际文件", 400);
  }
  const requireKind = async (ref: ProductionRef, kind: ProductionContent["kind"]) => {
    if ((await readRecord(directory, manifest, ref)).content.kind !== kind) failure(`成果引用必须指向 ${kind}`, 400);
  };
  if (content.kind === "episode") await requireKind(content.series, "series");
  if (content.kind === "dialogue") {
    const voice = await readRecord(directory, manifest, content.voice);
    if (voice.content.kind !== "voice" || voice.content.characterId !== content.speakerId) failure("对白说话人与音色身份不匹配", 400);
  }
  if (content.kind === "clip") {
    const plan = await readRecord(directory, manifest, content.plan);
    if (plan.content.kind !== "h3Plan" || !plan.content.segments.some(segment => segment.id === content.segmentId)) failure("视频片段没有对应的规划段", 400);
  }
  if (content.kind === "film") {
    for (const entry of content.entries) {
      await requireKind(entry.clip, "clip");
      if (entry.outSeconds <= entry.inSeconds) failure("片段结束时间必须晚于开始时间", 400);
    }
    for (const ref of content.dialogue) await requireKind(ref, "dialogue");
  }
  if (content.kind === "acceptance") {
    await requireKind(content.film, "film");
    if (content.status === "passed" && content.checks.some(check => check.result !== "passed")) failure("存在未通过的检查，不能标记验收通过", 400);
  }
}

async function staleDependencies(directory: string, manifest: ProductionManifest, record: ProductionRecord, seen = new Set<string>()): Promise<ProductionRef[]> {
  const key = `${record.id}:${record.revision}`;
  if (seen.has(key)) return [];
  seen.add(key);
  const stale: ProductionRef[] = [];
  for (const dependency of record.dependencies) {
    const adopted = manifest.adoptions.find(item => item.selected.id === dependency.id)?.selected;
    if (!sameRef(adopted, dependency)) stale.push(dependency);
    else stale.push(...await staleDependencies(directory, manifest, await readRecord(directory, manifest, dependency), seen));
  }
  return stale;
}

export async function read(directory: string, input: unknown) {
  const args = productionReadSchema.parse(input);
  const root = await realpath(directory);
  const manifest = await readManifest(root);
  if (args.action === "read") {
    const record = await readRecord(root, manifest, args.ref);
    return { record, adoption: manifest.adoptions.find(item => item.selected.id === record.id) ?? null, staleDependencies: await staleDependencies(root, manifest, record) };
  }
  if (args.action === "list") {
    const entries = manifest.objects.slice(args.offset, args.offset + args.limit);
    const items = await Promise.all(entries.map(async entry => {
      const latest = entry.revisions.at(-1)!;
      const record = await readRecord(root, manifest, { id: entry.id, revision: latest.revision });
      return { id: record.id, revision: record.revision, kind: record.content.kind, episodeId: record.episodeId, adoption: manifest.adoptions.find(item => item.selected.id === record.id) ?? null };
    }));
    const nextOffset = args.offset + entries.length;
    return { items, total: manifest.objects.length, nextOffset: nextOffset < manifest.objects.length ? nextOffset : null };
  }
  if (!manifest.objects.some(item => item.id === args.id)) failure("制作成果不存在", 404);
  const records: ProductionRecord[] = [];
  for (const entry of manifest.objects) {
    const latest = { id: entry.id, revision: entry.revisions.at(-1)!.revision };
    records.push(await readRecord(root, manifest, latest));
    const adopted = manifest.adoptions.find(item => item.selected.id === entry.id)?.selected;
    if (adopted && !sameRef(latest, adopted)) records.push(await readRecord(root, manifest, adopted));
  }
  // ACT: 项目对象上限 10000；用反向依赖队列遍历，避免递归全图重复扫描。
  const dependents = new Map<string, ProductionRecord[]>();
  for (const record of records) for (const dependency of record.dependencies) {
    const items = dependents.get(dependency.id) ?? [];
    items.push(record);
    dependents.set(dependency.id, items);
  }
  const queue = [args.id];
  const seen = new Set(queue);
  const seenRevisions = new Set<string>();
  const affected: ProductionRef[] = [];
  for (let index = 0; index < queue.length; index++) for (const record of dependents.get(queue[index]!) ?? []) {
    const key = `${record.id}:${record.revision}`;
    if (record.id === args.id || seenRevisions.has(key)) continue;
    seenRevisions.add(key);
    if (!seen.has(record.id)) { seen.add(record.id); queue.push(record.id); }
    affected.push({ id: record.id, revision: record.revision });
  }
  return { affected };
}

export async function execute(directory: string, input: unknown) {
  const args = productionExecuteSchema.parse(input);
  return updateManifest(directory, async (manifest, root) => {
    if (args.action === "adopt") {
      const record = await readRecord(root, manifest, args.ref);
      const current = manifest.adoptions.find(item => item.selected.id === record.id);
      if (!sameRef(current?.selected, args.expectedAdoption)) failure("采用结果已变化，请重新读取");
      if ((await staleDependencies(root, manifest, record)).length) failure("依赖尚未采用或已变化，请先确认上游成果");
      await validateContent(root, manifest, record.content);
      const adoption = { selected: args.ref, approval: args.approval, adoptedAt: new Date().toISOString() };
      manifest.adoptions = manifest.adoptions.filter(item => item.selected.id !== record.id);
      manifest.adoptions.push(adoption);
      return { adoption };
    }
    const entry = args.id ? manifest.objects.find(item => item.id === args.id) : undefined;
    if (args.id && !entry) failure("制作成果不存在", 404);
    if ((entry?.revisions.at(-1)?.revision ?? 0) !== args.expectedRevision) failure("成果已有新修订，请重新读取后再保存");
    const id = args.id ?? `item${crypto.randomUUID().replaceAll("-", "")}`;
    const revision = args.expectedRevision + 1;
    if (entry) {
      const previous = await readRecord(root, manifest, { id, revision: args.expectedRevision });
      if (previous.content.kind !== args.content.kind || previous.episodeId !== args.episodeId) failure("不能改变成果类型或所属集", 400);
    }
    if (args.episodeId) {
      const episode = manifest.objects.find(item => item.id === args.episodeId);
      if (!episode || (await readRecord(root, manifest, { id: episode.id, revision: episode.revisions.at(-1)!.revision })).content.kind !== "episode") failure("所属集不存在", 400);
    }
    if (args.dependencies.some(ref => ref.id === id)) failure("成果不能依赖自身", 400);
    if (new Set(args.dependencies.map(ref => ref.id)).size !== args.dependencies.length) failure("同一依赖只能引用一个修订", 400);
    for (const ref of args.dependencies) await readRecord(root, manifest, ref);
    for (const ref of contentReferences(args.content)) {
      if (!args.dependencies.some(dependency => sameRef(ref, dependency))) failure("内容引用必须同时登记为依赖", 400);
    }
    await validateContent(root, manifest, args.content);
    const record: ProductionRecord = { id, revision, episodeId: args.episodeId, dependencies: args.dependencies, content: args.content, createdAt: new Date().toISOString() };
    const path = await saveRecord(root, record);
    if (entry) entry.revisions.push({ revision, path });
    else manifest.objects.push({ id, revisions: [{ revision, path }] });
    return { record };
  });
}
