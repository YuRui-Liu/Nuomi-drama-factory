<template>
  <nodeSkeleton v-bind="nodeProps" style="width: 360px">
    <div class="styleCard nodrag nopan" @pointerdown.stop @dblclick.stop>
      <el-button @click="open">{{ draft?.name || "选择视觉风格" }} · {{ status }}</el-button>
      <img v-if="preview" :src="preview" :alt="`${draft?.name}示例图`" />
      <p v-if="draft">{{ draft.positive }}</p>
      <p v-if="error" role="alert">{{ error }}</p>
    </div>
  </nodeSkeleton>
  <el-dialog v-model="visible" title="视觉风格" width="min(850px, 90vw)" appendToBody :closeOnClickModal="false">
    <el-form v-if="draft" labelPosition="top" :disabled="busy">
      <el-form-item label="风格库">
        <div class="styleOptions">
          <button v-for="item in styleLibrary" :key="item.id" type="button" class="styleOption" :class="{ selected: preset === item.id }" :disabled="busy" :aria-pressed="preset === item.id" @click="selectPreset(item.id)">
            <img :src="stylePreviews[item.id]" :alt="`${item.content.name}示例图`" />
            <span>{{ item.content.name }}</span>
          </button>
        </div>
      </el-form-item>
      <el-form-item label="风格提示词"><el-input v-model="draft.positive" type="textarea" :autosize="{ minRows: 5, maxRows: 12 }" /></el-form-item>
      <p v-if="error" role="alert">{{ error }}</p>
    </el-form>
    <template #footer>
      <el-button :loading="busy" @click="save">保存</el-button>
      <el-button type="primary" :loading="busy" :disabled="!binding || dirty" @click="adoptDefault">采用为作品默认风格</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, inject, ref, watch } from "vue";
import { ElButton, ElDialog, ElForm, ElFormItem, ElInput } from "element-plus";
import { nodeSkeleton, nodeTools, useNode, z, type NodeHandle } from "@toonflow/nodes-scaffold/runtime";
import { productionRefSchema, styleContentSchema, styleInstruction } from "@toonflow/node-shared/production/schemas";
import type { Adoption, ProductionRecord, ProductionRef } from "@toonflow/node-shared/production/types";
import { styleLibrary } from "@toonflow/node-shared/production/styleLibrary";
import { stylePreviews } from "./stylePreviews";

type Style = Extract<ProductionRecord["content"], { kind: "style" }>;
type Result = { record: ProductionRecord; adoption: Adoption | null; staleDependencies: ProductionRef[] };
defineOptions({ inheritAttrs: false, handles: [{ id: "style", type: "source", dataType: "STRING", label: "风格提示词" }] satisfies NodeHandle[] });
const { node, nodeProps, outputs } = useNode({ label: "视觉风格", outputs: { style: { dataType: "STRING", value: "" } } });
const data = computed(() => node.data as typeof node.data & { productionRef?: ProductionRef });
const getDirectory = inject<() => string>("workspaceDirectory");
const binding = computed(() => productionRefSchema.safeParse(data.value.productionRef).data);
const visible = ref(false);
const busy = ref(false);
const error = ref("");
const preset = ref("");
const result = ref<Result>();
const defaultRef = ref<ProductionRef | null>(null);
const draft = ref<Style>();
const preview = computed(() => stylePreviews[preset.value]);
const dirty = computed(() => !!draft.value && JSON.stringify(draft.value) !== JSON.stringify(result.value?.record.content));
const status = computed(() => !result.value ? "候选" : defaultRef.value?.id === binding.value?.id && defaultRef.value?.revision === binding.value?.revision ? "作品默认" : result.value.adoption?.selected.revision === binding.value?.revision ? "已采用" : "候选");
function directory() { const value = getDirectory?.(); if (!value) throw new Error("请先选择作品工作目录"); return value; }
async function request(url: string, options?: RequestInit) {
  const response = await fetch(url, options);
  const value = await response.json();
  if (!response.ok || value.code !== 200) throw new Error(value.message || "风格操作失败");
  return value.data;
}
async function read(root: string, args: object): Promise<any> {
  return request(`/api/production/get?${new URLSearchParams({ directory: root, request: JSON.stringify(args) })}`);
}
async function execute(root: string, command: object) {
  return request("/api/production/execute", { method: "POST", headers: { "Content-Type": "application/json", "x-toonflow-workspace": "1" }, body: JSON.stringify({ directory: root, command }) });
}
async function refresh() {
  const root = directory();
  const refValue = binding.value;
  const current = await read(root, { action: "readDefaultStyle" });
  const value: Result | undefined = refValue ? await read(root, { action: "read", ref: refValue }) : undefined;
  if (root !== directory() || JSON.stringify(refValue) !== JSON.stringify(binding.value)) return;
  if (value && value.record.content.kind !== "style") throw new Error("绑定成果不是风格");
  defaultRef.value = current.ref;
  result.value = value;
  draft.value = value ? structuredClone(value.record.content as Style) : { kind: "style", name: "", positive: "", avoid: "", source: "", sourceVersion: "" };
  preset.value = styleLibrary.find(item => item.content.source === draft.value?.source)?.id ?? "";
  if (!value && styleLibrary[0]) selectPreset(styleLibrary[0].id);
  outputs.value.style.value = value && value.record.content.kind === "style" && value.adoption?.selected.revision === value.record.revision && !value.staleDependencies.length
    ? styleInstruction({ ref: { id: value.record.id, revision: value.record.revision }, mode: "override", snapshot: value.record.content }) : "";
}
async function open() { visible.value = true; try { await refresh(); } catch (reason) { error.value = reason instanceof Error ? reason.message : "风格读取失败"; } }
function selectPreset(id: string) { const item = styleLibrary.find(entry => entry.id === id); if (item) { preset.value = id; draft.value = { ...item.content }; } }
async function save() {
  if (!draft.value || busy.value) return;
  busy.value = true; error.value = "";
  try {
    const root = directory(); const previous = binding.value;
    const value = await execute(root, { action: "saveRevision", id: previous?.id, expectedRevision: previous?.revision ?? 0, dependencies: [], content: styleContentSchema.parse(draft.value) });
    if (root !== directory() || JSON.stringify(previous) !== JSON.stringify(binding.value)) throw new Error("风格已保存，但作品或节点已变化，请重新读取");
    data.value.productionRef = { id: value.record.id, revision: value.record.revision };
    await refresh();
  } catch (reason) { error.value = reason instanceof Error ? reason.message : "风格保存失败"; } finally { busy.value = false; }
}
async function adoptDefault() {
  if (!binding.value || busy.value || dirty.value) return;
  busy.value = true; error.value = "";
  try {
    const root = directory(); const selected = binding.value;
    const current: Result = await read(root, { action: "read", ref: selected });
    const defaultStyle = await read(root, { action: "readDefaultStyle" });
    if (root !== directory() || JSON.stringify(selected) !== JSON.stringify(binding.value)) throw new Error("作品或节点已变化，请重新读取");
    if (current.adoption?.selected.revision !== selected.revision) await execute(root, { action: "adopt", ref: selected, expectedAdoption: current.adoption?.selected ?? null, approval: { source: "user", evidence: "用户在风格节点点击采用" } });
    await execute(root, { action: "setDefaultStyle", ref: selected, expectedDefault: defaultStyle.ref });
    await refresh();
  } catch (reason) { error.value = reason instanceof Error ? reason.message : "风格采用失败"; } finally { busy.value = false; }
}
nodeTools.register({ name: "bindStyle", description: "绑定本作品的风格成果修订，不自动采用。", parameters: z.strictObject({ ref: productionRefSchema, expectedRef: productionRefSchema.nullable() }), async execute(args) {
  if (JSON.stringify(binding.value ?? null) !== JSON.stringify(args.expectedRef)) throw new Error("节点绑定已变化");
  const root = directory(); const value: Result = await read(root, { action: "read", ref: args.ref });
  if (value.record.content.kind !== "style" || root !== directory() || JSON.stringify(binding.value ?? null) !== JSON.stringify(args.expectedRef)) throw new Error("风格或作品已变化");
  data.value.productionRef = args.ref; await refresh(); return { ref: args.ref, adoption: value.adoption };
} });
nodeTools.register({ name: "readStyle", description: "读取节点绑定的风格、采用状态和作品默认风格。", parameters: z.strictObject({}), async execute() { await refresh(); return { result: result.value ?? null, defaultStyle: defaultRef.value }; } });
watch([binding, () => getDirectory?.()], () => { refresh().catch(reason => { error.value = reason instanceof Error ? reason.message : "风格读取失败"; }); }, { immediate: true });
</script>

<style scoped lang="scss">
.styleCard {
  img { display: block; width: 100%; max-height: 220px; object-fit: contain; margin-top: 12px; border-radius: 8px; }
  p { margin: 8px 0; white-space: pre-wrap; overflow-wrap: anywhere; max-height: 120px; overflow-y: auto; }
}
.styleOptions {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
  gap: 12px;
  width: 100%;
  max-height: 420px;
  overflow: auto;
  padding: 2px;
  .styleOption {
    padding: 0;
    overflow: hidden;
    border: 1px solid var(--el-border-color);
    border-radius: 6px;
    background: var(--el-fill-color-light);
    color: var(--el-text-color-primary);
    text-align: left;
    cursor: pointer;
    &.selected { outline: 2px solid var(--el-color-primary); outline-offset: -2px; }
    &:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: -2px; }
    &:disabled { cursor: not-allowed; }
    img { display: block; width: 100%; height: 84px; object-fit: cover; }
    span { display: block; padding: 8px 10px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  }
}
</style>
