import { scriptCreationApi } from "@/features/script-creation/api";
import { decodeBriefSettings } from "@/features/script-creation/settings";
import type { ScriptSettings } from "@/features/script-creation/types";

export async function readPlanningSettings(
  project: string,
  document: string,
  revision: string,
): Promise<ScriptSettings> {
  const revisions = await scriptCreationApi.revisions(project, document);
  const saved = revisions.find((item) => item.id === revision);
  if (!saved) {
    throw new Error(`未找到指定的创作简报版本（${revision}）`);
  }

  const settings = decodeBriefSettings(saved.markdown);
  if (!settings) {
    throw new Error(`指定的创作简报版本缺少有效的创作设定（${revision}）`);
  }

  return settings;
}
