import { z } from "zod";

const text = z.string().trim().min(1).max(100000);
export const productionIdSchema = z.string().regex(/^item[a-f0-9]{32}$/);
export const productionRefSchema = z.strictObject({ id: productionIdSchema, revision: z.number().int().positive() });
export const productionPathSchema = z.string().min(1).max(2048).refine(
  value => !/^[\\/]|[:\u0000-\u001f]/.test(value) && value.split(/[\\/]/).every(part => part && part !== "." && part !== ".."),
  "必须使用工作区内的相对路径",
);
const refs = z.array(productionRefSchema).max(1000);
const media = z.strictObject({ path: productionPathSchema, mimeType: text, durationSeconds: z.number().positive().optional() });
const idea = z.strictObject({
  kind: z.literal("idea"), oneLine: text, protagonistConflict: text, highlights: text, finalChoice: text,
});
const dialogue = z.strictObject({
  id: text, speakerId: text, text, direction: z.string().max(10000).optional(),
});
const h3Segment = z.strictObject({
  id: text,
  mode: z.enum(["t2va", "i2va", "fl2va", "l2va", "ref2va"]),
  sourceShotIds: z.array(text).min(1).max(100),
  dialogueIds: z.array(text).max(100),
  references: z.array(z.strictObject({ role: z.enum(["firstFrame", "lastFrame", "subject", "video", "voice"]), asset: productionRefSchema })).max(12),
  requestedSeconds: z.number().min(4).max(15),
  effectiveFrames: z.number().int().min(96).max(360),
  fps: z.literal(24),
  prompt: text,
  description: text,
}).superRefine((segment, context) => {
  const issue = (path: string, message: string) => context.addIssue({ code: "custom", path: [path], message });
  const count = (role: string) => segment.references.filter(reference => reference.role === role).length;
  const first = count("firstFrame");
  const last = count("lastFrame");
  const subjects = count("subject");
  const videos = count("video");
  const voices = count("voice");
  if (segment.mode === "ref2va") {
    if (first || last || !segment.references.length) issue("references", "全参考模式需要参考素材，不接受首尾帧槽位");
    if (subjects > 9 || videos > 3 || voices > 3) issue("references", "全参考模式最多 9 张图片、3 段视频、3 段音频，总计不超过 12 项");
  } else {
    const expected = { t2va: [0, 0], i2va: [1, 0], fl2va: [1, 1], l2va: [0, 1] }[segment.mode];
    if (first !== expected[0] || last !== expected[1] || subjects || videos || voices) issue("references", `${segment.mode} 的首尾帧素材数量不匹配，或包含不支持的参考槽位`);
  }
  for (const key of ["sourceShotIds", "dialogueIds"] as const) {
    if (new Set(segment[key]).size !== segment[key].length) issue(key, "同一生成段不能重复引用镜头或对白 ID");
  }
  const fields = segment.mode === "ref2va"
    ? ["subject_definitions", "summary", "retention_analysis", "detailed_description", "overall_soundscape", "non_diegetic_music"]
    : ["integrated_multimodal_description", "overall_soundscape", "non_diegetic_music"];
  const headings = [...segment.prompt.matchAll(/^(subject_definitions|summary|retention_analysis|detailed_description|integrated_multimodal_description|overall_soundscape|non_diegetic_music):[ \t]*/gm)];
  if (headings.map(match => match[1]).join(",") !== fields.join(",")) {
    issue("prompt", "H3 提示词必须按顺序包含该模式的全部字段，且不能重复");
  } else {
    for (let index = 0; index < headings.length; index++) {
      const heading = headings[index]!;
      const body = segment.prompt.slice(heading.index! + heading[0].length, headings[index + 1]?.index);
      if (!body.trim()) issue("prompt", `H3 提示词字段 ${heading[1]} 不能为空`);
    }
  }
});

export const productionContentSchema = z.discriminatedUnion("kind", [
  z.strictObject({ kind: z.literal("brief"), idea: text, constraints: z.string().max(100000) }),
  idea,
  z.strictObject({
    kind: z.literal("series"), title: text, premise: text, world: text,
    characters: z.array(z.strictObject({ id: text, name: text, description: text })).min(1).max(500),
    chapters: z.array(z.strictObject({ id: text, title: text, arc: text })).max(100),
    episodes: z.array(z.strictObject({ id: text, number: z.number().int().positive(), title: text, outline: text, chapterId: text.optional() })).min(1).max(1000),
  }),
  z.strictObject({ kind: z.literal("episode"), number: z.number().int().positive(), title: text, outline: text, series: productionRefSchema }),
  z.strictObject({
    kind: z.literal("script"), title: text,
    scenes: z.array(z.strictObject({
      id: text, location: text,
      shots: z.array(z.strictObject({ id: text, description: text, dialogue: z.array(dialogue).max(500) })).min(1).max(500),
    })).min(1).max(500),
  }),
  z.strictObject({ kind: z.literal("asset"), role: z.enum(["character", "scene", "prop"]), identityId: text, description: text, media }),
  z.strictObject({ kind: z.literal("voice"), characterId: text, description: text, source: z.enum(["designed", "imported"]), media }),
  z.strictObject({ kind: z.literal("dialogue"), dialogueId: text, speakerId: text, text, voice: productionRefSchema, media }),
  z.strictObject({ kind: z.literal("h3Plan"), segments: z.array(h3Segment).min(1).max(1000) }),
  z.strictObject({ kind: z.literal("clip"), plan: productionRefSchema, segmentId: text, taskId: text, media }),
  z.strictObject({
    kind: z.literal("film"), media, subtitlesPath: productionPathSchema,
    entries: z.array(z.strictObject({ clip: productionRefSchema, inSeconds: z.number().nonnegative(), outSeconds: z.number().positive() })).min(1).max(10000),
    dialogue: refs, audioPaths: z.array(productionPathSchema).max(1000),
  }),
  z.strictObject({
    kind: z.literal("acceptance"), film: productionRefSchema, status: z.enum(["needsReview", "passed"]),
    checks: z.array(z.strictObject({ name: text, result: z.enum(["passed", "failed", "unverified"]), evidencePaths: z.array(productionPathSchema).min(1).max(100), notes: text })).min(1).max(100),
  }),
]);

export const productionRecordSchema = z.strictObject({
  id: productionIdSchema, revision: z.number().int().positive(),
  episodeId: productionIdSchema.optional(), dependencies: refs,
  content: productionContentSchema, createdAt: z.iso.datetime(),
});
export const adoptionSchema = z.strictObject({
  selected: productionRefSchema,
  approval: z.strictObject({ source: z.enum(["user", "delegated"]), evidence: text }),
  adoptedAt: z.iso.datetime(),
});
export const productionManifestSchema = z.strictObject({
  version: z.literal(1),
  objects: z.array(z.strictObject({
    id: productionIdSchema,
    revisions: z.array(z.strictObject({ revision: z.number().int().positive(), path: productionPathSchema })).min(1),
  })).max(10000),
  adoptions: z.array(adoptionSchema).max(10000),
});
