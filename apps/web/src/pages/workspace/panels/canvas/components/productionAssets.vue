<template>
  <section class="productionAssets" aria-label="制作资产库">
    <el-select v-model="workId" clearable placeholder="全部作品" aria-label="筛选作品"><el-option v-for="work in catalog.works" :key="work.id" :label="work.title" :value="work.id" /></el-select>
    <el-button :loading="busy" @click="load">刷新</el-button>
    <el-button v-if="directory && !currentWork" :disabled="busy" @click="registerWork">登记当前作品</el-button>
    <p v-if="error" role="alert">{{ error }}</p>
    <el-scrollbar maxHeight="60vh">
      <article v-for="asset in assets" :key="asset.id" class="assetCard">
        <strong>{{ chosen(asset).name }} · {{ labels[asset.kind] }}</strong>
        <el-select :modelValue="chosen(asset).version" aria-label="版本" @update:modelValue="versions[asset.id] = Number($event)"><el-option v-for="version in asset.versions" :key="version.version" :label="`版本 ${version.version} · ${version.approval ? '已采用' : '候选'}`" :value="version.version" /></el-select>
        <audio v-if="asset.kind === 'voice'" :src="url(chosen(asset).path)" controls preload="none" />
        <img v-else :src="url(chosen(asset).path)" :alt="chosen(asset).name" loading="lazy" />
        <p>{{ catalog.works.find(work => work.id === asset.ownerWorkId)?.title }}</p>
        <details><summary>来源与版本依据</summary><p>{{ chosen(asset).reason }}</p></details>
        <p v-for="relation in chosen(asset).relations" :key="relation.relation + relation.target.assetId">{{ relationLabels[relation.relation] }}：{{ relatedName(relation.target) }}</p>
        <p v-if="useOf(asset.id)">当前作品使用版本 {{ useOf(asset.id)!.ref.version }}</p>
        <el-button v-if="currentWork?.id === asset.ownerWorkId && !chosen(asset).approval" :disabled="busy" @click="adopt(asset)">采用此版本</el-button>
        <el-button v-if="currentWork && chosen(asset).approval" :disabled="busy || useOf(asset.id)?.ref.version === chosen(asset).version" @click="use(asset)">用于当前作品</el-button>
      </article>
    </el-scrollbar>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ElButton, ElMessageBox, ElOption, ElScrollbar, ElSelect } from "element-plus";
import axios from "axios";
import { useWorkspaceStore } from "@/stores/workspace";
import { assetCatalogSchema, type AssetCatalog } from "@toonflow/node-shared/production/assetCatalog";
const props = defineProps<{ directory?: string }>();
const workspace = useWorkspaceStore();
const directory = computed(() => props.directory ?? workspace.project?.directory);
const catalog = ref<AssetCatalog>({ revision: 0, works: [], assets: [], uses: [] });
const workId = ref("");
const versions = ref<Record<string, number>>({});
const busy = ref(false);
const error = ref("");
const currentWork = computed(() => catalog.value.works.find(work => work.directory === directory.value));
const assets = computed(() => catalog.value.assets.filter(asset => !workId.value || asset.ownerWorkId === workId.value || catalog.value.uses.some(use => use.workId === workId.value && use.ref.assetId === asset.id)));
const labels = { character: "人物", characterVariant: "人物变体", scene: "场景", sceneVariant: "场景变体", prop: "道具", voice: "音色" };
const relationLabels = { variantOf: "基础资产", voiceOf: "对应人物", derivedFrom: "派生来源" };
const url = (path: string) => `/api/assets/read?path=${encodeURIComponent(path)}`;
type Asset = AssetCatalog["assets"][number];
function chosen(asset: Asset) {
  const selected = versions.value[asset.id] ?? useOf(asset.id)?.ref.version;
  return asset.versions.find(version => version.version === selected) ?? asset.versions.findLast(version => version.approval) ?? asset.versions.at(-1)!;
}
function relatedName(ref: { assetId: string; version: number }) { return `${catalog.value.assets.find(asset => asset.id === ref.assetId)?.versions.find(version => version.version === ref.version)?.name ?? '未找到资产'} · 版本 ${ref.version}`; }
function useOf(assetId: string) { return catalog.value.uses.find(use => use.workId === currentWork.value?.id && use.ref.assetId === assetId); }
async function load() {
  busy.value = true; error.value = "";
  try {
    const value = assetCatalogSchema.parse((await axios.get("/api/assets/catalog/get")).data.data);
    if (value.revision >= catalog.value.revision) catalog.value = value;
  }
  catch (reason) { error.value = reason instanceof Error ? reason.message : "读取失败"; }
  finally { busy.value = false; }
}
async function execute(command: Record<string, unknown>) {
  const root = directory.value;
  if (!root || busy.value) return;
  busy.value = true; error.value = "";
  try { await axios.post("/api/assets/catalog/execute", { directory: root, command: { ...command, expectedRevision: catalog.value.revision } }); await load(); }
  catch (reason) { error.value = reason instanceof Error ? reason.message : "保存失败"; }
  finally { busy.value = false; }
}
async function registerWork() { await execute({ action: "saveWork", title: workspace.project?.name ?? directory.value?.split(/[\\/]/).at(-1) ?? "作品" }); }
async function adopt(asset: Asset) {
  const version = chosen(asset); const root = directory.value;
  try { await ElMessageBox.confirm(`采用“${version.name}”版本 ${version.version}？`, "确认资产", { confirmButtonText: "采用", cancelButtonText: "取消" }); } catch { return; }
  if (root !== directory.value) return;
  await execute({ action: "adoptVersion", ref: { assetId: asset.id, version: version.version }, approval: { source: "user", evidence: `用户在资产库确认采用 ${version.name} 版本 ${version.version}` } });
}
async function use(asset: Asset) {
  const version = chosen(asset);
  await execute({ action: "useVersion", workId: currentWork.value!.id, ref: { assetId: asset.id, version: version.version }, approval: { source: "user", evidence: `用户点击用于当前作品：${version.name} 版本 ${version.version}` } });
}
watch(directory, load, { immediate: true });
</script>

<style scoped lang="scss">
.productionAssets {
  padding: 12px; min-width: 0; box-sizing: border-box;
  .assetCard {
    padding: 12px; margin: 12px 0; border: 1px solid var(--el-border-color); border-radius: 8px;
    overflow-wrap: anywhere;
    details { margin-bottom: 12px; }
    strong { display: block; margin-bottom: 8px; }
    img { display: block; max-width: 100%; max-height: 240px; margin: 12px auto; object-fit: contain; }
    audio { max-width: 100%; margin: 12px 0; }
  }
}
</style>
