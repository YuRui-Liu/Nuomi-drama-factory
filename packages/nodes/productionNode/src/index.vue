<template>
  <nodeSkeleton v-bind="nodeProps" style="width: 360px">
    <div v-if="binding" class="productionArtifact nodrag nopan" @pointerdown.stop @dblclick.stop>
      <el-button size="small" @click="open">{{ title }} · {{ status }}</el-button>
      <p>{{ summary }}</p>
      <img v-if="storyboardImage" :src="storyboardImage" alt="当前分镜" />
    </div>
  </nodeSkeleton>
    <el-dialog v-model="visible" :title="title" width="min(900px, 90vw)" appendToBody :closeOnClickModal="false" :beforeClose="close">
      <p v-if="error" role="alert">{{ error }}</p>
      <template v-if="result">
        <p>{{ status }} · 版本 {{ result.record.revision }}</p>
        <el-form v-if="draft" labelPosition="top" :disabled="saving">
          <el-form-item v-if="connectedStyles.length && ['imagePlan', 'storyboard', 'h3Plan'].includes(draft.kind)" label="连接的风格">
            <el-select v-model="selectedStyle"><el-option v-for="item in connectedStyles" :key="item.key" :label="item.label" :value="item.key" /></el-select>
          </el-form-item>
          <template v-if="draft.kind === 'brief'">
            <el-form-item label="创意"><el-input v-model="draft.idea" type="textarea" autosize /></el-form-item>
            <el-form-item label="制作约束"><el-input v-model="draft.constraints" type="textarea" autosize /></el-form-item>
          </template>
          <template v-else-if="draft.kind === 'idea'">
            <el-form-item label="一句话立意"><el-input v-model="draft.oneLine" type="textarea" autosize /></el-form-item>
            <el-form-item label="主角与冲突"><el-input v-model="draft.protagonistConflict" type="textarea" autosize /></el-form-item>
            <el-form-item label="看点"><el-input v-model="draft.highlights" type="textarea" autosize /></el-form-item>
            <el-form-item label="最终抉择"><el-input v-model="draft.finalChoice" type="textarea" autosize /></el-form-item>
          </template>
          <template v-else-if="draft.kind === 'imagePlan'">
            <el-form-item label="方案名称"><el-input v-model="draft.title" /></el-form-item>
            <p>目标：{{ draft.target.kind === 'asset' ? `${draft.target.role} · ${draft.target.identityId}` : `分镜 ${draft.target.shotId}` }}</p>
            <p v-if="draft.style">风格：{{ draft.style.snapshot.name }} · 版本 {{ draft.style.ref.revision }}</p>
            <el-button v-if="connectedStyles.length" :disabled="!selectedStyle" @click="applyStyle()">应用到出图方案</el-button>
            <el-form-item label="图片提示词"><el-input v-model="draft.prompt" type="textarea" autosize /></el-form-item>
          </template>
          <template v-else-if="draft.kind === 'script'">
            <el-form-item label="剧本标题"><el-input v-model="draft.title" /></el-form-item>
            <section v-for="scene in draft.scenes" :key="scene.id" class="scriptScene">
              <el-form-item label="场景"><el-input v-model="scene.location" /></el-form-item>
              <div v-for="shot in scene.shots" :key="shot.id">
                <el-form-item label="画面与行动"><el-input v-model="shot.description" type="textarea" autosize /></el-form-item>
                <el-form-item v-for="line in shot.dialogue" :key="line.id" :label="line.speakerId"><el-input v-model="line.text" type="textarea" autosize /></el-form-item>
              </div>
            </section>
          </template>
          <template v-else-if="draft.kind === 'episode'">
            <el-form-item label="分集标题"><el-input v-model="draft.title" /></el-form-item>
            <el-form-item label="分集大纲"><el-input v-model="draft.outline" type="textarea" autosize /></el-form-item>
          </template>
          <template v-else-if="draft.kind === 'storyboard'">
            <el-form-item label="分镜标题"><el-input v-model="draft.title" /></el-form-item>
            <el-form-item label="镜头"><el-select v-model="selectedShotId"><el-option v-for="shot in draft.shots" :key="shot.id" :label="shot.id" :value="shot.id" /></el-select></el-form-item>
            <template v-if="selectedShot">
              <img v-if="storyboardImage" class="storyboardImage" :src="storyboardImage" alt="当前分镜图" />
              <p>剧本来源：{{ selectedShot.sourceShotIds.join('、') }} · 对白：{{ selectedShot.dialogueIds.join('、') }}</p>
              <p v-if="selectedShot.style">风格：{{ selectedShot.style.snapshot.name }} · 版本 {{ selectedShot.style.ref.revision }}</p>
              <el-button v-if="connectedStyles.length" :disabled="!selectedStyle" @click="applyStyle(selectedShot.id)">应用到当前镜头</el-button>
              <el-form-item label="画面与动作"><el-input v-model="selectedShot.description" type="textarea" autosize /></el-form-item>
              <el-form-item label="预计时长（秒）"><el-input-number v-model="selectedShot.durationSeconds" :min="0.1" :max="15" :step="0.5" /></el-form-item>
              <el-form-item label="景别"><el-select v-model="selectedShot.framing"><el-option v-for="(label, value) in framingLabels" :key="value" :label="label" :value="value" /></el-select></el-form-item>
              <el-form-item v-if="selectedShot.staging.mode === 'direct'" label="简单特写直接制作依据"><el-input v-model="selectedShot.staging.reason" type="textarea" autosize /></el-form-item>
              <el-form-item v-else label="导演调度依据"><el-input v-model="selectedShot.staging.description" type="textarea" autosize /></el-form-item>
            </template>
          </template>
          <template v-else-if="draft.kind === 'series'">
            <el-form-item label="作品名称"><el-input v-model="draft.title" /></el-form-item>
            <el-form-item label="故事主线"><el-input v-model="draft.premise" type="textarea" autosize /></el-form-item>
            <el-form-item label="世界设定"><el-input v-model="draft.world" type="textarea" autosize /></el-form-item>
            <section v-for="character in draft.characters" :key="character.id">
              <el-form-item label="人物"><el-input v-model="character.name" /></el-form-item>
              <el-form-item label="人物设定"><el-input v-model="character.description" type="textarea" autosize /></el-form-item>
            </section>
            <section v-for="episode in draft.episodes" :key="episode.id">
              <el-form-item :label="`第 ${episode.number} 集`"><el-input v-model="episode.title" /></el-form-item>
              <el-form-item label="分集大纲"><el-input v-model="episode.outline" type="textarea" autosize /></el-form-item>
            </section>
          </template>
          <template v-else-if="draft.kind === 'h3Plan'">
            <el-form-item v-if="connectedTechniques.length" label="连接的案例手法"><el-select v-model="selectedTechnique"><el-option v-for="item in connectedTechniques" :key="item.key" :label="item.label" :value="item.key" /></el-select></el-form-item>
            <section v-for="segment in draft.segments" :key="segment.id">
              <p>{{ segment.id }} · {{ h3ModeLabels[segment.mode] }} · {{ (segment.effectiveFrames / segment.fps).toFixed(2) }} 秒</p>
              <p v-if="segment.style">风格：{{ segment.style.snapshot.name }} · 版本 {{ segment.style.ref.revision }}</p>
              <el-form-item v-if="connectedStyles.length" label="风格英文描述（包含视觉风格和避免项）"><el-input v-model="styleTranslations[segment.id]" type="textarea" autosize /></el-form-item>
              <el-button v-if="connectedStyles.length" :disabled="!selectedStyle" @click="applyStyle(segment.id)">应用到当前生成段</el-button>
              <p v-if="segment.technique">手法：{{ segment.technique.snapshot.title }} · 版本 {{ segment.technique.ref.revision }}</p>
              <p v-if="segment.technique?.snapshot.caseTitle">案例：{{ segment.technique.snapshot.caseTitle }}</p>
              <el-form-item v-if="connectedTechniques.length" label="当前镜头的英文手法描述"><el-input v-model="techniqueTranslations[segment.id]" type="textarea" autosize /></el-form-item>
              <el-button v-if="connectedTechniques.length" :disabled="!selectedTechnique" @click="applyTechnique(segment.id)">将手法应用到当前生成段</el-button>
              <el-form-item label="镜头动作"><el-input v-model="segment.description" type="textarea" autosize /></el-form-item>
              <details>
                <summary>素材依据与生成提示词</summary>
                <p v-for="(reference, index) in segment.references" :key="index">{{ reference.role }} · {{ reference.shotId || reference.asset.id }} · 版本 {{ reference.asset.revision }}</p>
                <el-form-item label="H3 提示词"><el-input v-model="segment.prompt" type="textarea" autosize /></el-form-item>
              </details>
            </section>
          </template>
          <p v-else>{{ summary }}</p>
        </el-form>
      </template>
      <template #footer>
        <el-button :disabled="saving" @click="reload">重新读取</el-button>
        <el-button v-if="editable" :loading="saving" @click="save">保存新版本</el-button>
      </template>
    </el-dialog>
</template>

<script setup lang="ts">
import { computed, inject, onBeforeUnmount, ref, watch } from "vue";
import { ElButton, ElDialog, ElForm, ElFormItem, ElInput, ElInputNumber, ElSelect, ElOption, ElMessageBox } from "element-plus";
import { useNode, nodeSkeleton, nodeTools, z, type NodeHandle } from "@toonflow/nodes-scaffold/runtime";
import { productionRefSchema, productionContentSchema, styleInstruction, h3StyleInstruction, techniqueInstruction, h3TechniqueInstruction } from "@toonflow/node-shared/production/schemas";
import { useNodeReferences } from "@toonflow/node-shared/useNodeReferences";
import type { ProductionRecord, ProductionRef, ProductionContent, Adoption } from "@toonflow/node-shared/production/types";

type Result = { record: ProductionRecord; adoption: Adoption | null; staleDependencies: ProductionRef[] };
defineOptions({ inheritAttrs: false, handles: [{ id: "in", type: "target", dataType: "STRING", label: "风格／手法输入" }] satisfies NodeHandle[] });
const { node, nodeProps, files } = useNode({ label: "制作成果" });
const { refList } = useNodeReferences();
const data = computed(() => node.data as typeof node.data & { productionRef?: ProductionRef });
const getDirectory = inject<() => string>("workspaceDirectory");
const binding = computed(() => productionRefSchema.safeParse(data.value.productionRef).data);
const visible = ref(false);
const saving = ref(false);
const error = ref("");
const result = ref<Result>();
const draft = ref<ProductionContent>();
const selectedShotId = ref("");
const selectedStyle = ref("");
const styleTranslations = ref<Record<string, string>>({});
const selectedTechnique = ref("");
const techniqueTranslations = ref<Record<string, string>>({});
const connectedStyles = computed(() => refList.value.flatMap(item => {
  if (item.dataType !== "STRING") return [];
  const match = item.value?.match(/^视觉风格 (item[a-f0-9]{32})@(\d+)：/);
  return match ? [{ key: `${match[1]}:${match[2]}`, label: `${match[1]} · 版本 ${match[2]}`, ref: { id: match[1]!, revision: Number(match[2]) } }] : [];
}));
const connectedTechniques = computed(() => refList.value.flatMap(item => {
  if (item.dataType !== "STRING") return [];
  const match = item.value?.match(/^镜头手法 (item[a-f0-9]{32})@(\d+)：([^\n]+)/);
  return match ? [{ key: `${match[1]}:${match[2]}`, label: match[3]!, ref: { id: match[1]!, revision: Number(match[2]) } }] : [];
}));
const framingLabels = { wide: "远景／全景", medium: "中景", closeUp: "特写", detail: "细节特写" };
const h3ModeLabels = { t2va: "文本生成", i2va: "首帧", fl2va: "首尾帧", l2va: "尾帧", ref2va: "图片参考" };
const selectedShot = computed(() => draft.value?.kind === "storyboard" ? draft.value.shots.find(shot => shot.id === selectedShotId.value) ?? draft.value.shots[0] : undefined);
const storyboardImage = files.useFileUrl(() => selectedShot.value?.image ? { url: selectedShot.value.image.path, mimeType: selectedShot.value.image.mimeType } : undefined, reason => { error.value = reason instanceof Error ? reason.message : "分镜图读取失败"; });
let requestNumber = 0;
let disposed = false;
onBeforeUnmount(() => { disposed = true; requestNumber++; });
const title = computed(() => {
  const content = result.value?.record.content;
  return content?.kind === "h3Plan" ? "H3 导演方案" : content && "title" in content ? content.title : "制作成果";
});
const summary = computed(() => {
  const content = result.value?.record.content;
  if (!content) return "";
  if (content.kind === "brief") return content.idea;
  if (content.kind === "idea") return content.oneLine;
  if (content.kind === "script") return content.scenes.map(scene => scene.location).join(" · ");
  if (content.kind === "episode") return content.outline;
  if (content.kind === "storyboard") return `${content.shots.length} 个镜头 · ${content.shots.reduce((sum, shot) => sum + shot.durationSeconds, 0)} 秒 · ${content.shots.filter(shot => shot.image).length} 张分镜图`;
  if (content.kind === "h3Plan") return `${content.segments.length} 段 · ${[...new Set(content.segments.map(segment => h3ModeLabels[segment.mode]))].join("／")} · 独立配音`;
  return "description" in content ? content.description : "premise" in content ? content.premise : "";
});
const editable = computed(() => draft.value && ["brief", "idea", "imagePlan", "script", "episode", "series", "storyboard", "h3Plan"].includes(draft.value.kind));
const dirty = computed(() => !!draft.value && !!result.value && JSON.stringify(draft.value) !== JSON.stringify(result.value.record.content));
const status = computed(() => error.value ? "读取失败" : !result.value ? "读取中" : result.value.staleDependencies.length ? "上游已变化" : result.value.adoption?.selected.revision === result.value.record.revision ? "已采用" : "候选");

function directory() {
  const value = getDirectory?.();
  if (!value) throw new Error("请先选择作品工作目录");
  return value;
}
async function read(root: string, ref: ProductionRef, signal?: AbortSignal): Promise<Result> {
  const query = new URLSearchParams({ directory: root, request: JSON.stringify({ action: "read", ref }) });
  return request(`/api/production/get?${query}`, { signal });
}
async function request(url: string, options?: RequestInit) {
  const response = await fetch(url, options);
  const value = await response.json();
  if (!response.ok || value.code !== 200) throw new Error(value.message || "制作操作失败");
  return value.data;
}
async function refresh() {
  const number = ++requestNumber;
  error.value = "";
  const ref = binding.value;
  result.value = undefined;
  draft.value = undefined;
  if (!ref) return;
  try {
    const root = directory();
    const value = await read(root, ref);
    if (number !== requestNumber || root !== directory()) return;
    result.value = value;
    draft.value = structuredClone(value.record.content);
    if (draft.value.kind === "h3Plan") {
      styleTranslations.value = Object.fromEntries(draft.value.segments.map(segment => [segment.id, segment.stylePrompt ?? ""]));
      techniqueTranslations.value = Object.fromEntries(draft.value.segments.map(segment => [segment.id, segment.techniquePrompt ?? ""]));
    }
    if (draft.value.kind === "storyboard" && !draft.value.shots.some(shot => shot.id === selectedShotId.value)) selectedShotId.value = draft.value.shots[0]?.id ?? "";
  } catch (reason) { if (number === requestNumber) error.value = reason instanceof Error ? reason.message : "读取成果失败"; }
}
async function open() { visible.value = true; await refresh(); }
async function discardChanges() {
  if (!dirty.value) return true;
  try {
    await ElMessageBox.confirm("放弃当前未保存的修改？", "未保存修改", { confirmButtonText: "放弃修改", cancelButtonText: "继续编辑", type: "warning" });
    return true;
  } catch { return false; }
}
async function close(done: () => void) { if (!saving.value && await discardChanges()) done(); }
async function reload() { if (!saving.value && await discardChanges()) await refresh(); }
function styles(content: ProductionContent) {
  if (content.kind === "imagePlan" || content.kind === "asset") return content.style ? [content.style] : [];
  if (content.kind === "storyboard") return content.shots.flatMap(shot => shot.style ? [shot.style] : []);
  if (content.kind === "h3Plan") return content.segments.flatMap(segment => segment.style ? [segment.style] : []);
  return [];
}
async function applyStyle(targetId?: string) {
  if (!draft.value || !selectedStyle.value) return;
  try {
    const selected = connectedStyles.value.find(item => item.key === selectedStyle.value);
    if (!selected) throw new Error("连接的风格已变化，请重新选择");
    const root = directory(); const previousBinding = JSON.stringify(binding.value); const value = await read(root, selected.ref);
    if (root !== directory() || previousBinding !== JSON.stringify(binding.value) || selectedStyle.value !== selected.key || !connectedStyles.value.some(item => item.key === selected.key)) throw new Error("作品、成果或风格连接已变化");
    if (value.record.content.kind !== "style" || value.adoption?.selected.revision !== selected.ref.revision || value.staleDependencies.length) throw new Error("请先采用连接的风格修订");
    const style = { ref: selected.ref, mode: "override" as const, snapshot: value.record.content };
    if (draft.value.kind === "storyboard") {
      const shot = draft.value.shots.find(item => item.id === targetId);
      if (!shot) throw new Error("请选择目标镜头");
      if (shot.image && (!shot.style || JSON.stringify(shot.style.ref) !== JSON.stringify(style.ref))) throw new Error("已有分镜图不能直接改写风格，请先确认或重做该镜头图像");
      shot.style = style;
    } else if (draft.value.kind === "imagePlan") {
      const previous = draft.value.style ? styleInstruction(draft.value.style) : "";
      draft.value.prompt = `${previous ? draft.value.prompt.replace(previous, "").trim() : draft.value.prompt.trim()}\n\n${styleInstruction(style)}`;
      draft.value.style = style;
    } else if (draft.value.kind === "h3Plan") {
      const segment = draft.value.segments.find(item => item.id === targetId);
      if (!segment) throw new Error("请选择目标生成段");
      const translation = styleTranslations.value[segment.id]?.trim();
      if (!translation || /\p{Script=Han}/u.test(translation) || !/[a-z]/i.test(translation)) throw new Error("请根据所选风格填写英文视觉描述和避免项");
      const previous = segment.style ? segment.stylePrompt ? h3StyleInstruction(segment.style, segment.stylePrompt) : styleInstruction(segment.style) : "";
      const field = segment.mode === "ref2va" ? "detailed_description:" : "integrated_multimodal_description:";
      const prompt = previous ? segment.prompt.replace(previous, "") : segment.prompt;
      if (!prompt.includes(field)) throw new Error("H3 提示词缺少画面描述字段");
      segment.prompt = prompt.replace(field, `${field}\n${h3StyleInstruction(style, translation)}\n`);
      segment.style = style;
      segment.stylePrompt = translation;
    }
    error.value = "";
  } catch (reason) { error.value = reason instanceof Error ? reason.message : "应用风格失败"; }
}
async function applyTechnique(targetId: string) {
  if (draft.value?.kind !== "h3Plan" || !selectedTechnique.value) return;
  try {
    const selected = connectedTechniques.value.find(item => item.key === selectedTechnique.value);
    if (!selected) throw new Error("连接的手法已变化，请重新选择");
    const root = directory(); const previousBinding = JSON.stringify(binding.value); const value = await read(root, selected.ref);
    if (root !== directory() || previousBinding !== JSON.stringify(binding.value) || !connectedTechniques.value.some(item => item.key === selected.key)) throw new Error("作品、成果或手法连接已变化");
    if (value.record.content.kind !== "technique" || value.adoption?.selected.revision !== selected.ref.revision || value.staleDependencies.length) throw new Error("请先采用连接的手法修订");
    if (draft.value?.kind !== "h3Plan") throw new Error("目标方案已变化");
    const segment = draft.value.segments.find(item => item.id === targetId);
    if (!segment) throw new Error("请选择目标生成段");
    const mode = { t2va: null, l2va: null, i2va: "i2v", fl2va: "fl2v", ref2va: "ref_only" }[segment.mode];
    if (!mode || !value.record.content.modes.includes(mode as "i2v" | "fl2v" | "ref_only")) throw new Error("手法与当前 H3 模式不兼容");
    const duration = segment.effectiveFrames / segment.fps;
    if (duration < value.record.content.minSeconds || duration > value.record.content.maxSeconds) throw new Error("手法与当前 H3 时长不兼容");
    const translation = techniqueTranslations.value[segment.id]?.trim();
    if (!translation || /\p{Script=Han}/u.test(translation) || !/[a-z]/i.test(translation)) throw new Error("请结合当前镜头与案例填写英文手法描述");
    const next = { ref: selected.ref, snapshot: value.record.content };
    const field = segment.mode === "ref2va" ? "detailed_description:" : "integrated_multimodal_description:";
    const previous = segment.technique ? segment.techniquePrompt ? h3TechniqueInstruction(segment.technique, segment.techniquePrompt) : techniqueInstruction(segment.technique) : "";
    const prompt = previous ? segment.prompt.replace(previous, "") : segment.prompt;
    if (!prompt.includes(field)) throw new Error("H3 提示词缺少画面描述字段");
    segment.prompt = prompt.replace(field, `${field}\n${h3TechniqueInstruction(next, translation)}\n`);
    segment.technique = next;
    segment.techniquePrompt = translation;
    error.value = "";
  } catch (reason) { error.value = reason instanceof Error ? reason.message : "应用手法失败"; }
}
async function save() {
  if (!result.value || !draft.value || saving.value) return;
  saving.value = true;
  error.value = "";
  try {
    const root = directory();
    const record = result.value.record;
    const previousBinding = JSON.stringify(binding.value);
    const oldStyles = styles(record.content); const newStyles = styles(draft.value);
    const dependencies = record.dependencies.filter(ref => !oldStyles.some(style => style.ref.id === ref.id));
    for (const style of newStyles) if (!dependencies.some(ref => ref.id === style.ref.id)) dependencies.push(style.ref);
    const oldTechniques = record.content.kind === "h3Plan" ? record.content.segments.flatMap(segment => segment.technique ? [segment.technique.ref] : []) : [];
    const newTechniques = draft.value.kind === "h3Plan" ? draft.value.segments.flatMap(segment => segment.technique ? [segment.technique.ref] : []) : [];
    for (const ref of oldTechniques) if (!newTechniques.some(item => item.id === ref.id)) { const index = dependencies.findIndex(item => item.id === ref.id); if (index >= 0) dependencies.splice(index, 1); }
    for (const ref of newTechniques) { const index = dependencies.findIndex(item => item.id === ref.id); if (index < 0) dependencies.push(ref); else if (dependencies[index]?.revision !== ref.revision) throw new Error("同一手法不能同时使用不同版本"); }
    const response = await request("/api/production/execute", { method: "POST", headers: { "Content-Type": "application/json", "x-toonflow-workspace": "1" }, body: JSON.stringify({ directory: root, command: {
      action: "saveRevision", id: record.id, expectedRevision: record.revision, episodeId: record.episodeId,
      dependencies, content: productionContentSchema.parse(draft.value),
    } }) });
    if (disposed || root !== directory() || JSON.stringify(binding.value) !== previousBinding) throw new Error("新版本已保存，但作品或节点绑定已变化，请在原作品重新读取");
    const saved: ProductionRecord = response.record;
    data.value.productionRef = { id: saved.id, revision: saved.revision };
    await refresh();
  } catch (reason) { error.value = reason instanceof Error ? reason.message : "保存失败"; }
  finally { saving.value = false; }
}

nodeTools.register({
  name: "bindProduction",
  description: "将当前节点绑定到本作品实际制作成果的固定修订；只保存引用，不复制正文或自动采用。expectedRef 用于防止覆盖其他绑定。",
  parameters: z.strictObject({ ref: productionRefSchema, expectedRef: productionRefSchema.nullable() }),
  async execute(args, { signal }) {
    if (saving.value) throw new Error("成果正在保存");
    const previous = JSON.stringify(binding.value ?? null);
    if (previous !== JSON.stringify(args.expectedRef)) throw new Error("节点成果绑定已变化，请重读");
    const root = directory();
    const value = await read(root, args.ref, signal);
    if (!["brief", "idea", "imagePlan", "series", "episode", "script", "storyboard", "h3Plan"].includes(value.record.content.kind)) throw new Error("当前工作台支持立意、出图方案、全剧规划、剧本、分镜及 H3 方案；其他媒体成果应使用对应媒体工作台");
    signal?.throwIfAborted();
    if (disposed || root !== directory() || previous !== JSON.stringify(binding.value ?? null)) throw new Error("作品或节点绑定已变化");
    data.value.productionRef = args.ref;
    return { ref: args.ref, kind: value.record.content.kind, adoption: value.adoption };
  },
});
nodeTools.register({
  name: "readProduction",
  description: "读取当前节点绑定的制作成果正文、采用状态和上游变化；缺少绑定时返回 null。",
  parameters: z.strictObject({}),
  async execute(_args, { signal }) { return binding.value ? read(directory(), binding.value, signal) : null; },
});
watch([binding, () => getDirectory?.()], refresh, { immediate: true });
</script>

<style scoped lang="scss">
.productionArtifact {
  margin-top: 6px;
  img { display: block; width: 100%; max-height: 200px; object-fit: contain; }
  p { display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
  .scriptScene { padding: 12px 0; border-top: 1px solid var(--el-border-color); }
}
.storyboardImage { display: block; max-width: 100%; max-height: 420px; object-fit: contain; }
</style>
