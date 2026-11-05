export type AgentReadiness = 'unknown' | 'running' | 'choose_agent' | 'configure_model' | 'model_unverified' | 'choose_scope' | 'ready';
export function agentReadiness(input: { unknown: boolean; running: boolean; hasAgent: boolean; modelAvailable: boolean; modelUnverified?: boolean; hasScope: boolean }): AgentReadiness {
  if (input.unknown) return 'unknown';
  if (input.running) return 'running';
  if (!input.hasAgent) return 'choose_agent';
  if (!input.modelAvailable) return 'configure_model';
  if (input.modelUnverified) return 'model_unverified';
  if (!input.hasScope) return 'choose_scope';
  return 'ready';
}
export const readinessLabels: Record<AgentReadiness, string> = {
  unknown: '运行状态待确认', running: '主创正在执行', choose_agent: '请先选择项目主创',
  configure_model: '文本模型待配置', model_unverified: '文本模型已配置，尚未验证',
  choose_scope: '请选择本次目标范围', ready: '主创已就绪',
};
import type { useAgentRuntime } from './useAgentRuntime';

export function runtimeReadiness(runtime: ReturnType<typeof useAgentRuntime>): AgentReadiness {
  return agentReadiness({
    unknown: !runtime.statusKnown || runtime.intent?.phase === 'pending',
    running: runtime.busy,
    hasAgent: !!runtime.main?.current_agent,
    modelAvailable: !!runtime.runtime?.available,
    modelUnverified: runtime.runtime?.code === 'configured_not_verified',
    hasScope: Boolean(runtime.canvasScope || runtime.nodes.length > 0),
  });
}
