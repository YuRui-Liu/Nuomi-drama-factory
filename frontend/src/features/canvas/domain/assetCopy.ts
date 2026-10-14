/** Copies are independent material, not new server-authorized asset uses. */
export function independentAssetCopy<T>(input: T): T {
  if (!input || typeof input !== 'object' || !('assetRef' in input) || !input.assetRef) return input;
  const data = structuredClone(input) as Record<string, unknown>;
  data.fixedAssetOrigin = { reference: data.assetRef, selection: data.assetMediaSelection };
  for (const key of ['assetRef', 'assetMediaSelection', 'assetSceneManifest', 'slot_target', 'committed_slot_url', 'committed_at', '__freezone_source', 'taskKey', 'generationTask']) delete data[key];
  data.displayName = `${String(data.displayName ?? '资产')} · 独立副本`;
  data.isGenerating = false;
  return data as T;
}
