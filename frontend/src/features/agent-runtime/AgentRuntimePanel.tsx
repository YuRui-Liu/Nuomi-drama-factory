import { AgentRunStatus } from './AgentRunStatus';
import { DelegationComposer } from './DelegationComposer';
import { ProjectCrewDialog } from './ProjectCrewDialog';
import type { useAgentRuntime } from './useAgentRuntime';

/**
 * Compatibility composition for standalone runtime views and existing callers.
 * Managed chat uses the same focused components in its compact layout.
 */
export function AgentRuntimePanel({
  runtime,
}: {
  runtime: ReturnType<typeof useAgentRuntime>;
}) {
  return (
    <div className="max-h-[35vh] shrink-0 space-y-3 overflow-y-auto border-b border-white/10 p-3 text-xs">
      <ProjectCrewDialog runtime={runtime} embedded />
      <DelegationComposer
        runtime={runtime}
        goal=""
        onGoalChange={() => undefined}
        showGoal={false}
        showAction={false}
        embedded
      />
      <AgentRunStatus runtime={runtime} compact={false} />
    </div>
  );
}
