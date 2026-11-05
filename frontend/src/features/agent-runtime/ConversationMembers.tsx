import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import {
  getDiscussionMembers,
  joinDiscussionMember,
  leaveDiscussionMember,
} from "@/api/collaboration";
import type { AgentAdoption } from "@/api/reusableAgents";
import { useAuthStore } from "@/stores/auth-store";

const displayName = (profile: Record<string, unknown> | null | undefined) =>
  typeof profile?.name === "string" && profile.name.trim() ? profile.name : "未命名成员";

export function ConversationMembers({
  project,
  canvas,
  conversation,
  adoptions,
  active,
  onTargetChange,
}: {
  project: string;
  canvas: string;
  conversation: string;
  adoptions: AgentAdoption[];
  active: boolean;
  onTargetChange: (adoptionId: string | null) => void;
}) {
  const username = useAuthStore((state) => state.username);
  const queryClient = useQueryClient();
  const key = useMemo(
    () => ["discussion-members", username, project, canvas, conversation] as const,
    [canvas, conversation, project, username],
  );
  const query = useQuery({
    queryKey: key,
    queryFn: () => getDiscussionMembers(project, {
      canvas_id: canvas,
      conversation_id: conversation,
    }),
    enabled: active && !!username,
    retry: false,
  });
  const members = query.data?.members ?? [];
  const [adoptionToJoin, setAdoptionToJoin] = useState("");
  const [target, setTarget] = useState("");
  const availableAdoptions = adoptions.filter((item) =>
    !members.some((member) => member.adoption_id === item.adoption_id),
  );

  useEffect(() => {
    onTargetChange(target && members.some((member) => member.adoption_id === target) ? target : null);
  }, [members, onTargetChange, target]);

  const refresh = () => queryClient.invalidateQueries({ queryKey: key });
  const join = useMutation({
    mutationFn: (requested?: { adoptionId: string; revision: number }) => {
      const adoption = requested
        ? { adoption_id: requested.adoptionId, revision: requested.revision }
        : availableAdoptions.find((item) => item.adoption_id === adoptionToJoin);
      if (!adoption) throw new Error("请选择已采用的项目成员");
      return joinDiscussionMember(project, { canvas_id: canvas, conversation_id: conversation }, adoption.adoption_id, adoption.revision);
    },
    onSuccess: async () => {
      setAdoptionToJoin("");
      await refresh();
    },
  });
  const leave = useMutation({
    mutationFn: (adoptionId: string) => leaveDiscussionMember(
      project,
      { canvas_id: canvas, conversation_id: conversation },
      adoptionId,
    ),
    onSuccess: async (_result, adoptionId) => {
      if (target === adoptionId) setTarget("");
      await refresh();
    },
  });

  const main = query.data?.main;
  return (
    <section aria-label="会话常驻成员" className="rounded-xl border border-white/10 bg-white/[0.025] p-3 text-xs">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="font-medium text-foreground">本会话成员</p>
          <p className="text-muted-foreground">只在此画布会话生效；项目采用目录不会自动加入。</p>
        </div>
        <button type="button" className="text-muted-foreground underline" disabled={!active || query.isFetching} onClick={() => void query.refetch()}>
          {query.isFetching ? "读取中…" : "刷新"}
        </button>
      </div>
      {query.error && <p role="alert" className="mt-2 text-destructive">无法读取会话成员：{query.error instanceof Error ? query.error.message : "请求失败"}</p>}
      {!query.error && (
        <div className="mt-3 space-y-2">
          <p className="rounded-md bg-white/[0.04] px-2 py-1.5">
            <span className="font-medium">主创：</span>{main ? displayName(main.profile) : query.isLoading ? "读取中…" : "默认创作助手（可直接讨论）"}
            {typeof main?.profile.responsibilities === "string" && <span className="ml-2 text-muted-foreground">{main.profile.responsibilities}</span>}
          </p>
          {members.length === 0 ? (
            <p className="text-muted-foreground">暂无常驻成员。需要点名回复时，可从已采用的项目成员中加入。</p>
          ) : (
            <ul className="space-y-2">
              {members.map((member) => {
                const currentAdoption = adoptions.find((item) => item.adoption_id === member.adoption_id);
                const hasNewerRevision = !!currentAdoption && currentAdoption.revision > member.adoption_revision;
                return (
                  <li key={member.adoption_id} className="flex items-start gap-2 rounded-md bg-white/[0.04] px-2 py-2">
                    <div className="min-w-0 flex-1">
                      <p className="font-medium">{displayName(member.profile)} <span className="font-normal text-muted-foreground">· 常驻成员 v{member.adoption_revision}</span></p>
                      {typeof member.profile.responsibilities === "string" && <p className="mt-0.5 whitespace-pre-wrap text-muted-foreground">{member.profile.responsibilities}</p>}
                      {hasNewerRevision && currentAdoption && (
                        <div className="mt-2 flex flex-wrap items-center gap-2">
                          <p className="text-muted-foreground">项目已升级到修订 {currentAdoption.revision}；本会话仍固定修订 {member.adoption_revision}。</p>
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            className="h-7 px-2"
                            disabled={!active || join.isPending}
                            onClick={() => join.mutate({ adoptionId: currentAdoption.adoption_id, revision: currentAdoption.revision })}
                          >
                            刷新会话成员到修订 {currentAdoption.revision}
                          </Button>
                        </div>
                      )}
                    </div>
                    <Button type="button" variant="ghost" size="sm" className="h-7 shrink-0 px-2" disabled={!active || leave.isPending} onClick={() => leave.mutate(member.adoption_id)}>
                      退出
                    </Button>
                  </li>
                );
              })}
            </ul>
          )}
          {availableAdoptions.length > 0 && (
            <div className="flex gap-2 pt-1">
              <select aria-label="加入会话的项目成员" value={adoptionToJoin} onChange={(event) => setAdoptionToJoin(event.target.value)} disabled={!active || join.isPending} className="min-w-0 flex-1 rounded-md border border-white/10 bg-[#111518] px-2 py-1.5">
                <option value="">选择已采用成员</option>
                {availableAdoptions.map((adoption) => <option key={adoption.adoption_id} value={adoption.adoption_id}>{adoption.effective.name} · 项目 v{adoption.revision}</option>)}
              </select>
              <Button type="button" variant="outline" size="sm" disabled={!active || !adoptionToJoin || join.isPending} onClick={() => join.mutate(undefined)}>
                {join.isPending ? "加入中…" : "加入会话"}
              </Button>
            </div>
          )}
          {join.error && <p role="alert" className="text-destructive">加入失败：{join.error.message}</p>}
          {leave.error && <p role="alert" className="text-destructive">退出失败：{leave.error.message}</p>}
          <label className="grid gap-1 pt-1 text-muted-foreground">
            回复成员
            <select aria-label="选择本次回复成员" value={target} onChange={(event) => setTarget(event.target.value)} disabled={!active} className="min-h-9 rounded-md border border-white/10 bg-[#111518] px-2 text-foreground">
              <option value="">{main ? "项目主创（默认）" : "默认创作助手"}</option>
              {members.map((member) => <option key={member.adoption_id} value={member.adoption_id}>{displayName(member.profile)} · 会话成员</option>)}
            </select>
          </label>
        </div>
      )}
    </section>
  );
}
