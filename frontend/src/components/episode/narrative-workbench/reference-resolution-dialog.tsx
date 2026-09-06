import { useMemo, useState } from "react";
import type { NarrativeReferenceDecision, NarrativeReferenceRequirement } from "@/lib/queries/narrative-groups";
import { MatchedReferenceSection } from "./matched-reference-section";
import { UnresolvedReferenceSection } from "./unresolved-reference-section";

const resolvedStatuses = new Set(["matched", "fallback", "temporary", "ignored"]);

export function ReferenceResolutionDialog({ requirements, onChange, onCreateProp }: {
  requirements: NarrativeReferenceRequirement[];
  onChange: (decisions: NarrativeReferenceDecision[], unresolvedCount: number) => void;
  onCreateProp?: (requirement: NarrativeReferenceRequirement) => void;
}) {
  const [decisions, setDecisions] = useState<NarrativeReferenceDecision[]>([]);
  const unresolved = useMemo(() => requirements.filter((item) => !resolvedStatuses.has(item.status)), [requirements]);
  const matched = useMemo(() => requirements.filter((item) => resolvedStatuses.has(item.status)), [requirements]);
  const decide = (decision: NarrativeReferenceDecision) => {
    const next = [...decisions.filter((item) => item.requirement_id !== decision.requirement_id), decision];
    setDecisions(next);
    onChange(next, unresolved.filter((item) => !next.some((entry) => entry.requirement_id === item.id)).length);
  };
  return <div className="space-y-3">
    <UnresolvedReferenceSection requirements={unresolved} decisions={decisions} onDecide={decide} onCreateProp={onCreateProp} />
    <MatchedReferenceSection requirements={matched} />
  </div>;
}
