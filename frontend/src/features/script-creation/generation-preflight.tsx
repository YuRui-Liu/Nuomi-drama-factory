import { useTaskRuntimeConfig } from "@/lib/queries/model-gateway";

export function GenerationPreflight({ project }: { project: string }) {
  const query = useTaskRuntimeConfig();
  const route = query.data?.data.roles.find(role => role.id === "script_creation")?.route;
  return <section aria-label="创作生成前确认" className="space-y-1 rounded border border-white/15 p-3 text-xs text-white/70">
    <p>执行方式：{route?.runtime || "暂未知，请检查模型设置"}</p>
    <p>创作模型：{route?.model || "暂未知，请检查模型设置"}</p>
    <p>生成及质量审查可能调用模型多次；计费单位与金额暂未知，请核对账户计价规则。</p>
    <a className="underline" href={`/projects/${encodeURIComponent(project)}/costs`}>查看项目费用与计价规则</a>
  </section>;
}
