<template>
  <nodeSkeleton v-bind="nodeProps" style="width: 360px">
    <div class="ideaCard nodrag nopan" @pointerdown.stop @dblclick.stop>
      <el-button @click="open">立意</el-button>
      <p v-if="adopted">{{ adopted.record.content.kind === "idea" ? adopted.record.content.oneLine : "" }}</p>
      <p v-if="error" role="alert">{{ error }}</p>
    </div>
  </nodeSkeleton>
  <el-dialog v-model="visible" title="立意" width="min(850px, 90vw)" appendToBody :closeOnClickModal="false">
    <el-form labelPosition="top" :disabled="busy">
      <el-form-item label="创意"><el-input v-model="brief.idea" type="textarea" autosize /></el-form-item>
      <el-form-item label="制作约束"><el-input v-model="brief.constraints" type="textarea" autosize /></el-form-item>
      <el-button :loading="busy" @click="saveBrief">保存创意</el-button>
      <el-form-item label="立意方向">
        <el-select v-model="selectedIndex" placeholder="选择方向" @change="selectIdea">
          <el-option v-for="(item, index) in candidates" :key="item.ref.id" :label="item.content.oneLine" :value="index" />
        </el-select>
      </el-form-item>
      <el-form-item label="一句话立意"><el-input v-model="draft.oneLine" type="textarea" autosize /></el-form-item>
      <el-form-item label="主角与核心冲突"><el-input v-model="draft.protagonistConflict" type="textarea" autosize /></el-form-item>
      <el-form-item label="故事看点"><el-input v-model="draft.highlights" type="textarea" autosize /></el-form-item>
      <el-form-item label="最终抉择"><el-input v-model="draft.finalChoice" type="textarea" autosize /></el-form-item>
      <p v-if="error" role="alert">{{ error }}</p>
    </el-form>
    <template #footer><el-button :loading="busy" @click="saveIdea">保存立意</el-button><el-button type="primary" :disabled="!selected || dirty" :loading="busy" @click="adoptIdea">采用立意</el-button></template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, inject, ref, watch } from "vue";
import { ElButton, ElDialog, ElForm, ElFormItem, ElInput, ElOption, ElSelect } from "element-plus";
import { nodeSkeleton, nodeTools, useNode, z, type NodeHandle } from "@toonflow/nodes-scaffold/runtime";
import { productionContentSchema, productionRefSchema } from "@toonflow/node-shared/production/schemas";
import type { Adoption, ProductionContent, ProductionRecord, ProductionRef } from "@toonflow/node-shared/production/types";

type Idea = Extract<ProductionContent, { kind: "idea" }>;
type Result = { record: ProductionRecord; adoption: Adoption | null; staleDependencies: ProductionRef[] };
type Candidate = { ref: ProductionRef; content: Idea; adoption: Adoption | null };
defineOptions({ inheritAttrs: false, handles: [{ id: "idea", type: "source", dataType: "STRING", label: "已采用立意" }] satisfies NodeHandle[] });
const { node, nodeProps, outputs } = useNode({ label: "立意", outputs: { idea: { dataType: "STRING", value: "" } } });
const data = computed(() => node.data as typeof node.data & { briefRef?: ProductionRef; ideaRefs?: ProductionRef[]; adoptedIdea?: ProductionRef });
const getDirectory = inject<() => string>("workspaceDirectory");
const visible = ref(false);
const busy = ref(false);
const error = ref("");
const brief = ref({ kind: "brief" as const, idea: "", constraints: "" });
const draft = ref<Idea>({ kind: "idea", oneLine: "", protagonistConflict: "", highlights: "", finalChoice: "" });
const candidates = ref<Candidate[]>([]);
const selectedIndex = ref<number>();
const selected = computed(() => selectedIndex.value === undefined ? undefined : candidates.value[selectedIndex.value]);
const adopted = ref<Result>();
const dirty = computed(() => !selected.value || JSON.stringify(draft.value) !== JSON.stringify(selected.value.content));
function directory() { const root = getDirectory?.(); if (!root) throw new Error("请先选择作品工作目录"); return root; }
async function request(url: string, options?: RequestInit) { const response = await fetch(url, options); const value = await response.json(); if (!response.ok || value.code !== 200) throw new Error(value.message || "立意操作失败"); return value.data; }
async function read(root: string, ref: ProductionRef): Promise<Result> { return request(`/api/production/get?${new URLSearchParams({ directory: root, request: JSON.stringify({ action: "read", ref }) })}`); }
async function execute(root: string, command: object) { return request("/api/production/execute", { method: "POST", headers: { "Content-Type": "application/json", "x-toonflow-workspace": "1" }, body: JSON.stringify({ directory: root, command }) }); }
async function refresh() {
  const root = directory();
  const state = JSON.stringify({ briefRef: data.value.briefRef, ideaRefs: data.value.ideaRefs, adoptedIdea: data.value.adoptedIdea });
  const [briefResult, ideaResults, adoptedResult] = await Promise.all([
    data.value.briefRef ? read(root, data.value.briefRef) : undefined,
    Promise.all((data.value.ideaRefs ?? []).map(ref => read(root, ref))),
    data.value.adoptedIdea ? read(root, data.value.adoptedIdea) : undefined,
  ]);
  if (root !== directory() || state !== JSON.stringify({ briefRef: data.value.briefRef, ideaRefs: data.value.ideaRefs, adoptedIdea: data.value.adoptedIdea })) return;
  if (briefResult?.record.content.kind === "brief") brief.value = structuredClone(briefResult.record.content);
  candidates.value = ideaResults.map(value => { if (value.record.content.kind !== "idea") throw new Error("候选成果不是立意"); return { ref: { id: value.record.id, revision: value.record.revision }, content: value.record.content, adoption: value.adoption }; });
  adopted.value = adoptedResult;
  outputs.value.idea.value = adoptedResult?.record.content.kind === "idea" && adoptedResult.adoption?.selected.revision === adoptedResult.record.revision && !adoptedResult.staleDependencies.length ? JSON.stringify(adoptedResult.record.content) : "";
  selectedIndex.value = candidates.value.findIndex(item => item.ref.id === data.value.adoptedIdea?.id && item.ref.revision === data.value.adoptedIdea?.revision);
  if (selectedIndex.value < 0) selectedIndex.value = candidates.value.length ? 0 : undefined;
  if (selected.value) draft.value = structuredClone(selected.value.content);
}
async function open() { visible.value = true; try { await refresh(); } catch (reason) { error.value = reason instanceof Error ? reason.message : "立意读取失败"; } }
function selectIdea() { if (selected.value) draft.value = structuredClone(selected.value.content); }
async function saveBrief() {
  if (busy.value) return; busy.value = true; error.value = "";
  try { const root = directory(); const previous = data.value.briefRef; const value = await execute(root, { action: "saveRevision", id: previous?.id, expectedRevision: previous?.revision ?? 0, dependencies: [], content: productionContentSchema.parse(brief.value) }); if (root !== directory() || JSON.stringify(previous) !== JSON.stringify(data.value.briefRef)) throw new Error("创意已保存，但作品或节点已变化"); data.value.briefRef = { id: value.record.id, revision: value.record.revision }; }
  catch (reason) { error.value = reason instanceof Error ? reason.message : "创意保存失败"; } finally { busy.value = false; }
}
async function saveIdea() {
  if (busy.value) return; busy.value = true; error.value = "";
  try { const root = directory(); const previous = selected.value?.ref; const refs = JSON.stringify(data.value.ideaRefs ?? []); const value = await execute(root, { action: "saveRevision", id: previous?.id, expectedRevision: previous?.revision ?? 0, dependencies: [], content: productionContentSchema.parse(draft.value) }); if (root !== directory() || refs !== JSON.stringify(data.value.ideaRefs ?? [])) throw new Error("立意已保存，但作品或节点已变化"); const next = { id: value.record.id, revision: value.record.revision }; data.value.ideaRefs = [...(data.value.ideaRefs ?? []).filter(item => item.id !== next.id), next]; await refresh(); selectedIndex.value = candidates.value.findIndex(item => item.ref.id === next.id); selectIdea(); }
  catch (reason) { error.value = reason instanceof Error ? reason.message : "立意保存失败"; } finally { busy.value = false; }
}
async function adoptIdea() {
  if (!selected.value || dirty.value || busy.value) return; busy.value = true; error.value = "";
  try { const root = directory(); const candidate = selected.value; await execute(root, { action: "adopt", ref: candidate.ref, expectedAdoption: candidate.adoption?.selected ?? null, approval: { source: "user", evidence: "用户在立意节点点击采用" } }); if (root !== directory()) throw new Error("立意已采用，但作品已变化"); data.value.adoptedIdea = candidate.ref; await refresh(); }
  catch (reason) { error.value = reason instanceof Error ? reason.message : "立意采用失败"; } finally { busy.value = false; }
}
nodeTools.register({ name: "bindIdeas", description: "将已保存的创意与立意候选绑定到节点，不自动采用。", parameters: z.strictObject({ brief: productionRefSchema.nullable(), candidates: z.array(productionRefSchema).max(3) }), async execute(args) { const root = directory(); const values = await Promise.all([...(args.brief ? [read(root, args.brief)] : []), ...args.candidates.map(item => read(root, item))]); if (root !== directory()) throw new Error("作品已变化"); if (args.brief && values[0]?.record.content.kind !== "brief") throw new Error("创意引用类型错误"); if (values.slice(args.brief ? 1 : 0).some(item => item.record.content.kind !== "idea")) throw new Error("候选引用类型错误"); data.value.briefRef = args.brief ?? undefined; data.value.ideaRefs = args.candidates; await refresh(); return { brief: args.brief, candidates: args.candidates }; } });
nodeTools.register({ name: "readIdeas", description: "读取节点的创意、立意候选及已采用立意。", parameters: z.strictObject({}), async execute() { await refresh(); return { brief: data.value.briefRef ?? null, candidates: candidates.value, adopted: adopted.value ?? null }; } });
watch(() => getDirectory?.(), () => { refresh().catch(reason => { error.value = reason instanceof Error ? reason.message : "立意读取失败"; }); }, { immediate: true });
</script>

<style scoped lang="scss">
.ideaCard { p { margin: 8px 0; white-space: pre-wrap; } }
</style>
