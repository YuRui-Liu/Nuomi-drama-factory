<template>
  <nodeSkeleton v-bind="nodeProps" style="width: 360px">
    <div class="techniqueCard nodrag nopan" @pointerdown.stop @dblclick.stop>
      <el-button @click="open">{{ current?.title || "选择案例手法" }}</el-button>
      <p v-if="current">{{ current.camera }}</p>
      <p v-if="error" role="alert">{{ error }}</p>
    </div>
  </nodeSkeleton>
  <el-dialog v-model="visible" title="案例手法" width="min(850px, 90vw)" appendToBody :closeOnClickModal="false">
    <el-form labelPosition="top" :disabled="busy">
      <el-form-item label="手法"><el-select v-model="selectedId" filterable placeholder="选择手法"><el-option v-for="item in techniqueLibrary" :key="item.id" :value="item.id" :label="`${item.category} · ${item.title}`" /></el-select></el-form-item>
      <template v-if="selected">
        <p>{{ selected.summary }}</p>
        <p>动作：{{ selected.actionBeats.join('；') }}</p>
        <p>运镜：{{ selected.camera }}</p>
        <p>结束构图：{{ selected.ending }}</p>
        <p>适用模式：{{ selected.modes.join('、') }} · {{ selected.minSeconds }}–{{ selected.maxSeconds }} 秒</p>
        <el-form-item label="案例"><el-select v-model="caseId" clearable filterable placeholder="搜索或选择案例"><el-option-group label="关联案例"><el-option v-for="item in relatedCases" :key="item.id" :value="item.id" :label="item.title" /></el-option-group><el-option-group label="全部案例"><el-option v-for="item in techniqueCases" :key="item.id" :value="item.id" :label="item.title" /></el-option-group></el-select></el-form-item>
        <template v-if="selectedCase"><p>{{ selectedCase.summary }}</p><p v-for="(prompt, index) in selectedCase.prompts" :key="index" class="casePrompt">{{ prompt.text }}</p><a v-if="selectedCase.source" :href="selectedCase.source" target="_blank" rel="noopener noreferrer">查看案例来源</a></template>
      </template>
      <p v-if="error" role="alert">{{ error }}</p>
    </el-form>
    <template #footer><el-button type="primary" :disabled="!selected" :loading="busy" @click="adopt">采用手法</el-button></template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, inject, ref, watch } from "vue";
import { ElButton, ElDialog, ElForm, ElFormItem, ElOption, ElOptionGroup, ElSelect } from "element-plus";
import { nodeSkeleton, nodeTools, useNode, z, type NodeHandle } from "@toonflow/nodes-scaffold/runtime";
import { productionRefSchema, techniqueContentSchema, techniqueInstruction } from "@toonflow/node-shared/production/schemas";
import { techniqueLibrary, techniqueCases } from "@toonflow/node-shared/production/techniqueLibrary";
import type { Adoption, ProductionContent, ProductionRecord, ProductionRef } from "@toonflow/node-shared/production/types";

type Technique = Extract<ProductionContent, { kind: "technique" }>;
type Result = { record: ProductionRecord; adoption: Adoption | null; staleDependencies: ProductionRef[] };
defineOptions({ inheritAttrs: false, handles: [{ id: "technique", type: "source", dataType: "STRING", label: "案例手法" }] satisfies NodeHandle[] });
const { node, nodeProps, outputs } = useNode({ label: "案例手法", outputs: { technique: { dataType: "STRING", value: "" } } });
const data = computed(() => node.data as typeof node.data & { productionRef?: ProductionRef });
const getDirectory = inject<() => string>("workspaceDirectory");
const binding = computed(() => productionRefSchema.safeParse(data.value.productionRef).data);
const visible = ref(false); const busy = ref(false); const error = ref(""); const selectedId = ref(""); const caseId = ref("");
const current = ref<Technique>(); const selected = computed(() => techniqueLibrary.find(item => item.id === selectedId.value));
const relatedCases = computed(() => techniqueCases.filter(item => selected.value?.caseIds.includes(item.id)));
const selectedCase = computed(() => techniqueCases.find(item => item.id === caseId.value));
watch(selectedId, () => { caseId.value = ""; }, { flush: "sync" });
function directory() { const root = getDirectory?.(); if (!root) throw new Error("请先选择作品工作目录"); return root; }
async function request(url: string, options?: RequestInit) { const response = await fetch(url, options); const value = await response.json(); if (!response.ok || value.code !== 200) throw new Error(value.message || "手法操作失败"); return value.data; }
async function read(root: string, ref: ProductionRef): Promise<Result> { return request(`/api/production/get?${new URLSearchParams({ directory: root, request: JSON.stringify({ action: "read", ref }) })}`); }
async function execute(root: string, command: object) { return request("/api/production/execute", { method: "POST", headers: { "Content-Type": "application/json", "x-toonflow-workspace": "1" }, body: JSON.stringify({ directory: root, command }) }); }
async function refresh() {
  const root = directory(); const ref = binding.value;
  const value = ref ? await read(root, ref) : undefined;
  if (root !== directory() || JSON.stringify(ref) !== JSON.stringify(binding.value)) return;
  if (value && value.record.content.kind !== "technique") throw new Error("绑定成果不是案例手法");
  current.value = value?.record.content as Technique | undefined;
  outputs.value.technique.value = current.value && ref && value?.adoption?.selected.revision === ref.revision && !value.staleDependencies.length ? techniqueInstruction({ ref, snapshot: current.value }) : "";
}
async function open() { visible.value = true; try { await refresh(); selectedId.value = current.value?.id ?? ""; if (current.value?.caseId) caseId.value = current.value.caseId; } catch (reason) { error.value = reason instanceof Error ? reason.message : "手法读取失败"; } }
async function adopt() {
  if (!selected.value || busy.value) return; busy.value = true; error.value = "";
  try {
    const root = directory(); const previous = binding.value; const item = selected.value; const related = selectedCase.value;
    const content = techniqueContentSchema.parse({ kind: "technique", id: item.id, title: item.title, intent: item.intent, actionBeats: item.actionBeats, performance: item.performance, camera: item.camera, ending: item.ending, avoid: item.avoid, modes: item.modes, minSeconds: item.minSeconds, maxSeconds: item.maxSeconds, source: item.source, ...(related ? { caseId: related.id, caseTitle: related.title, caseSource: related.source, casePrompts: related.prompts.filter(prompt => prompt.text?.trim()).map(prompt => ({ text: prompt.text, provenance: prompt.provenance ?? "", source: prompt.source ?? "" })) } : {}) });
    const value = await execute(root, { action: "saveRevision", id: previous?.id, expectedRevision: previous?.revision ?? 0, dependencies: [], content });
    if (root !== directory() || JSON.stringify(previous) !== JSON.stringify(binding.value)) throw new Error("手法已保存，但作品或节点已变化");
    const next = { id: value.record.id, revision: value.record.revision };
    data.value.productionRef = next;
    const current = await read(root, next);
    await execute(root, { action: "adopt", ref: next, expectedAdoption: current.adoption?.selected ?? null, approval: { source: "user", evidence: "用户在案例手法节点点击采用" } });
    await refresh();
  } catch (reason) { error.value = reason instanceof Error ? reason.message : "手法采用失败"; } finally { busy.value = false; }
}
nodeTools.register({ name: "bindTechnique", description: "绑定已有手法成果，不自动采用。", parameters: z.strictObject({ ref: productionRefSchema, expectedRef: productionRefSchema.nullable() }), async execute(args) { const root = directory(); if (JSON.stringify(binding.value ?? null) !== JSON.stringify(args.expectedRef)) throw new Error("节点绑定已变化"); const value = await read(root, args.ref); if (root !== directory() || value.record.content.kind !== "technique" || JSON.stringify(binding.value ?? null) !== JSON.stringify(args.expectedRef)) throw new Error("手法或作品已变化"); data.value.productionRef = args.ref; await refresh(); return { ref: args.ref, adoption: value.adoption }; } });
nodeTools.register({ name: "readTechnique", description: "读取已绑定手法和采用状态。", parameters: z.strictObject({}), async execute() { await refresh(); return { ref: binding.value ?? null, content: current.value ?? null }; } });
nodeTools.register({ name: "searchTechniques", description: "按镜头目的、运镜或案例名称查找手法和案例；结果只作为推荐，须由用户确认。", parameters: z.strictObject({ query: z.string().trim().min(1).max(200) }), async execute(args) { const query = args.query.toLowerCase(); return { techniques: techniqueLibrary.filter(item => `${item.title} ${item.category} ${item.summary} ${item.camera}`.toLowerCase().includes(query)).slice(0, 20), cases: techniqueCases.filter(item => `${item.title} ${item.summary}`.toLowerCase().includes(query)).slice(0, 20) }; } });
nodeTools.register({ name: "readTechniqueCase", description: "读取案例详情及来源提示词，用于参考镜头写法。", parameters: z.strictObject({ id: z.string().min(1) }), async execute(args) { const item = techniqueCases.find(entry => entry.id === args.id); if (!item) throw new Error("案例不存在"); return item; } });
watch([binding, () => getDirectory?.()], () => { refresh().catch(reason => { error.value = reason instanceof Error ? reason.message : "手法读取失败"; }); }, { immediate: true });
</script>

<style scoped lang="scss">
.techniqueCard { p { margin: 8px 0; } }
.casePrompt { white-space: pre-wrap; max-height: 240px; overflow: auto; }
</style>
