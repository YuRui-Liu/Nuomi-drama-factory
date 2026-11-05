import { lstat, realpath } from "@toonflow/file";
import { productionExecuteSchema, productionReadSchema } from "@toonflow/node-shared/production/operations";
import { styleInstruction, h3StyleInstruction, h3TechniqueInstruction } from "@toonflow/node-shared/production/schemas";
import type { ProductionContent, ProductionManifest, ProductionRecord, ProductionRef } from "@toonflow/node-shared/production/types";
import { resolveWorkspacePath } from "@/utils/workspace/files";
import { failure, readManifest, readRecord, saveRecord, updateManifest } from "./store";

function sameRef(left: ProductionRef | null | undefined, right: ProductionRef | null | undefined) {
  return left?.id === right?.id && left?.revision === right?.revision;
}

function styleBindings(content: ProductionContent) {
  if (content.kind === "asset" || content.kind === "imagePlan") return content.style ? [content.style] : [];
  if (content.kind === "storyboard") return content.shots.flatMap(shot => shot.style ? [shot.style] : []);
  if (content.kind === "h3Plan") return content.segments.flatMap(segment => segment.style ? [segment.style] : []);
  return [];
}

export function referenceMedia(content: ProductionContent, shotId?: string) {
  if (content.kind === "storyboard") {
    const image = content.shots.find(shot => shot.id === shotId)?.image;
    if (!image) failure("H3 分镜参考必须指定存在且已有图片的镜头 ID", 400);
    return image;
  }
  if (shotId || !("media" in content)) failure("H3 参考必须指向媒体成果或具体分镜图", 400);
  return content.media;
}

function contentReferences(content: ProductionContent): ProductionRef[] {
  const styles = styleBindings(content).map(binding => binding.ref);
  switch (content.kind) {
    case "episode": return [content.series];
    case "storyboard": return [content.script, ...styles];
    case "dialogue": return [content.voice];
    case "clip": return [content.plan];
    case "film": return [...content.entries.map(entry => entry.clip), ...content.dialogue];
    case "acceptance": return [content.film];
    case "h3Plan": return [...styles, ...content.segments.flatMap(segment => [...segment.references.map(reference => reference.asset), ...(segment.technique ? [segment.technique.ref] : [])])];
    default: return styles;
  }
}

function mediaPaths(content: ProductionContent) {
  const paths = "media" in content ? [content.media.path] : [];
  if (content.kind === "film") paths.push(content.subtitlesPath, ...content.audioPaths);
  if (content.kind === "acceptance") paths.push(...content.checks.flatMap(check => check.evidencePaths));
  if (content.kind === "storyboard") paths.push(...content.shots.flatMap(shot => [
    ...shot.image ? [shot.image.path] : [],
    ...shot.staging.mode === "director" ? [shot.staging.previs.path] : [],
  ]));
  return paths;
}

async function validateContent(directory: string, manifest: ProductionManifest, content: ProductionContent) {
  if (content.kind === "h3Plan") for (const segment of content.segments) {
    if (!segment.technique) continue;
    const binding = segment.technique;
    const record = await readRecord(directory, manifest, binding.ref);
    if (record.content.kind !== "technique" || JSON.stringify(record.content) !== JSON.stringify(binding.snapshot)) failure("手法快照与引用的修订不一致", 400);
    if (!sameRef(manifest.adoptions.find(item => item.selected.id === binding.ref.id)?.selected, binding.ref)) failure("手法修订尚未采用或已变化", 400);
    const mode = { t2va: null, l2va: null, i2va: "i2v", fl2va: "fl2v", ref2va: "ref_only" }[segment.mode];
    if (!mode || !binding.snapshot.modes.includes(mode as "i2v" | "fl2v" | "ref_only")) failure("手法与 H3 生成模式不兼容", 400);
    const duration = segment.effectiveFrames / segment.fps;
    if (duration < binding.snapshot.minSeconds || duration > binding.snapshot.maxSeconds) failure("手法与 H3 生成时长不兼容", 400);
    const field = segment.mode === "ref2va" ? "detailed_description:" : "integrated_multimodal_description:";
    const body = segment.prompt.split(field)[1]?.split(/\n(?:overall_soundscape|non_diegetic_music):/)[0] ?? "";
    if (!segment.techniquePrompt) failure("H3 手法缺少根据当前镜头和案例整理的英文执行描述", 400);
    if (!body.includes(h3TechniqueInstruction(binding, segment.techniquePrompt)) || [...segment.prompt.matchAll(/Camera direction \(item[a-f0-9]{32}@\d+\):/g)].length !== 1) failure("H3 画面描述必须包含且只包含目标手法的英文执行描述", 400);
  }
  if (manifest.defaultStyle) {
    if ((content.kind === "asset" || content.kind === "imagePlan") && !content.style) failure("作品已有默认风格，正式成果必须记录风格快照", 400);
    if (content.kind === "storyboard" && content.shots.some(shot => !shot.style)) failure("作品已有默认风格，分镜须逐镜记录风格快照", 400);
    if (content.kind === "h3Plan" && content.segments.some(segment => !segment.style)) failure("作品已有默认风格，H3 方案须逐段记录风格快照", 400);
  }
  for (const binding of styleBindings(content)) {
    const record = await readRecord(directory, manifest, binding.ref);
    if (record.content.kind !== "style" || JSON.stringify(record.content) !== JSON.stringify(binding.snapshot)) failure("风格快照与引用的修订不一致", 400);
    if (!sameRef(manifest.adoptions.find(item => item.selected.id === binding.ref.id)?.selected, binding.ref)) failure("风格修订尚未采用或已变化", 400);
    if (binding.mode === "default" && !sameRef(manifest.defaultStyle, binding.ref)) failure("作品默认风格已变化", 400);
  }
  if (content.kind === "imagePlan" && content.style && (!content.prompt.includes(styleInstruction(content.style)) || [...content.prompt.matchAll(/视觉风格 item[a-f0-9]{32}@\d+：/g)].length !== 1)) failure("图片提示词必须包含且只包含目标风格快照", 400);
  if (content.kind === "imagePlan" && content.target.kind === "storyboardShot") {
    const target = content.target;
    const storyboard = await readRecord(directory, manifest, target.storyboard);
    if (storyboard.content.kind !== "storyboard") failure("出图目标必须是分镜成果", 400);
    const shot = storyboard.content.shots.find(item => item.id === target.shotId);
    if (!shot) failure("出图目标不是该分镜修订中的镜头", 400);
    if (content.style && !sameRef(shot.style?.ref, content.style.ref)) failure("出图风格与目标镜头风格不一致", 400);
    if (manifest.objects.find(item => item.id === target.storyboard.id)?.revisions.at(-1)?.revision !== target.storyboard.revision) failure("出图目标分镜已有新修订", 400);
  }
  if (content.kind === "h3Plan") for (const segment of content.segments) {
    if (!segment.style) continue;
    const field = segment.mode === "ref2va" ? "detailed_description" : "integrated_multimodal_description";
    const body = segment.prompt.split(`${field}:`)[1]?.split(/\n(?:overall_soundscape|non_diegetic_music):/)[0] ?? "";
    if (!segment.stylePrompt) failure("H3 风格缺少英文执行描述，请根据风格快照填写");
    if (!body.includes(h3StyleInstruction(segment.style, segment.stylePrompt)) || [...segment.prompt.matchAll(/Visual style \(item[a-f0-9]{32}@\d+\):/g)].length !== 1) failure("H3 画面描述必须包含且只包含目标风格的英文执行描述", 400);
  }
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
  if (content.kind === "storyboard") {
    const script = (await readRecord(directory, manifest, content.script)).content;
    if (script.kind !== "script") failure("分镜必须引用剧本", 400);
    const sourceShots = script.scenes.flatMap(scene => scene.shots);
    unique(content.shots.map(shot => shot.id), "分镜 ID");
    for (const shot of content.shots) {
      unique(shot.sourceShotIds, "分镜来源 ID");
      unique(shot.dialogueIds, "分镜对白 ID");
      if (shot.sourceShotIds.some(id => !sourceShots.some(source => source.id === id))) failure("分镜引用的剧本段落不存在", 400);
      const dialogueIds = sourceShots.filter(source => shot.sourceShotIds.includes(source.id)).flatMap(source => source.dialogue.map(line => line.id));
      if (shot.dialogueIds.some(id => !dialogueIds.includes(id))) failure("分镜对白必须来自对应剧本段落", 400);
      if (shot.staging.mode === "direct" && (shot.complexStaging || !["closeUp", "detail"].includes(shot.framing))) failure("只有简单特写可说明依据后跳过导演台", 400);
      if (shot.image && !shot.image.mimeType.startsWith("image/")) failure("分镜成果必须是图片", 400);
      if (shot.staging.mode === "director" && !shot.staging.previs.mimeType.startsWith("image/") && !shot.staging.previs.mimeType.startsWith("video/")) failure("导演依据必须是构图或预演媒体", 400);
    }
  }
  if (content.kind === "h3Plan") {
    unique(content.segments.map(item => item.id), "生成段 ID");
    for (const segment of content.segments) {
      const durations = { video: 0, voice: 0 };
      for (const reference of segment.references) {
        const source = (await readRecord(directory, manifest, reference.asset)).content;
        const media = referenceMedia(source, reference.shotId);
        const expectedType = reference.role === "video" ? "video/" : reference.role === "voice" ? "audio/" : "image/";
        if (!media.mimeType.startsWith(expectedType)) failure(`H3 ${reference.role} 槽位的媒体类型不匹配`, 400);
        const target = await resolveWorkspacePath(directory, media.path);
        if (!(await lstat(target.path)).isFile()) failure("H3 参考媒体文件不存在", 400);
        if (reference.role === "video" || reference.role === "voice") {
          const duration = media.durationSeconds;
          if (duration === undefined || duration < 2 || duration > 15) failure("H3 音视频参考须有 2–15 秒的时长记录；提交前仍须实测媒体", 400);
          durations[reference.role] += duration;
        }
      }
      if (durations.video > 15 || durations.voice > 15) failure("H3 视频参考或音频参考的总时长不能超过 15 秒", 400);
    }
  }
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
  if (record.content.kind === "imagePlan" && record.content.target.kind === "storyboardShot") {
    const target = record.content.target.storyboard;
    if (manifest.objects.find(item => item.id === target.id)?.revisions.at(-1)?.revision !== target.revision) stale.push(target);
  }
  for (const binding of styleBindings(record.content)) if (binding.mode === "default" && !sameRef(binding.ref, manifest.defaultStyle)) stale.push(binding.ref);
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
  if (args.action === "readDefaultStyle") {
    const ref = manifest.defaultStyle;
    return ref ? { ref, record: await readRecord(root, manifest, ref), adoption: manifest.adoptions.find(item => item.selected.id === ref.id) ?? null } : { ref: null, record: null, adoption: null };
  }
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
  for (const record of records) for (const dependency of [...record.dependencies, ...(record.content.kind === "imagePlan" && record.content.target.kind === "storyboardShot" ? [record.content.target.storyboard] : [])]) {
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
    if (args.action === "setDefaultStyle") {
      if (!sameRef(manifest.defaultStyle, args.expectedDefault)) failure("作品默认风格已变化，请重新读取");
      const record = await readRecord(root, manifest, args.ref);
      if (record.content.kind !== "style") failure("默认风格必须引用风格成果", 400);
      if (!sameRef(manifest.adoptions.find(item => item.selected.id === args.ref.id)?.selected, args.ref)) failure("请先采用该风格修订", 400);
      manifest.defaultStyle = args.ref;
      return { defaultStyle: args.ref };
    }
    if (args.action === "adopt") {
      const record = await readRecord(root, manifest, args.ref);
      const current = manifest.adoptions.find(item => item.selected.id === record.id);
      if (!sameRef(current?.selected, args.expectedAdoption)) failure("采用结果已变化，请重新读取");
      if ((await staleDependencies(root, manifest, record)).length) failure("依赖尚未采用或已变化，请先确认上游成果");
      await validateContent(root, manifest, record.content);
      if (record.content.kind === "storyboard" && record.content.shots.some(shot => !shot.image)) failure("分镜图尚未齐全，不能采用正式分镜");
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
