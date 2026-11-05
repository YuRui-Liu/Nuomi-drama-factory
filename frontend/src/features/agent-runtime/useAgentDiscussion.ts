// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "@/api/client";
import {
  getAgentDiscussion,
  postAgentDiscussion,
  type AgentDiscussionSelection,
  type AgentDiscussionAvailability,
  type AgentDiscussionMessage,
  type AgentDiscussionStatus,
} from "@/api/agentDiscussions";
import { captureFreezoneCanvasRuntime, reportFreezoneCanvasConflict } from "@/features/freezone/canvasSyncRuntime";
import { refreshAgentCanvasChanges } from "@/features/canvas/infrastructure/orchestrationGateway";
import { useAuthStore } from "@/stores/auth-store";
import {
  associateDiscussionCanvasBaseline,
  saveDiscussionCanvasBaseline,
} from "./runOperation";
import type { RunOwner } from "@/api/agentRuns";

export interface AgentDiscussionOwner {
  project: string;
  canvas: string;
  conversation: string;
}

export interface AgentDiscussionPendingRequest {
  requestId: string;
  status: Extract<AgentDiscussionStatus, "pending" | "submission_unknown">;
  message: string;
}

export interface AgentDiscussionSendOptions {
  targetAdoptionId?: string | null;
  selection?: AgentDiscussionSelection;
}

export interface AgentDiscussionState {
  messages: AgentDiscussionMessage[];
  availability: AgentDiscussionAvailability | null;
  loading: boolean;
  busy: boolean;
  error: string;
  pendingRequests: AgentDiscussionPendingRequest[];
  pendingRequest: AgentDiscussionPendingRequest | null;
  send: (message: string, options?: AgentDiscussionSendOptions) => Promise<boolean>;
  refresh: () => Promise<void>;
}

interface SavedIntent {
  requestId: string;
  status: "pending" | "submission_unknown";
  message: string;
}

const DISCUSSION_QUERY = ["agent-discussion"] as const;
const SAVED_INTENT_PREFIX = "supertale:agent-discussion:intent:";
const INTENT_STORAGE_ERROR = "浏览器本地存储不可用，讨论内容未提交；请保留草稿后检查存储空间。";

function identityKey(username: string, owner: AgentDiscussionOwner): string {
  return JSON.stringify([
    username,
    owner.project,
    owner.canvas,
    owner.conversation,
  ]);
}

function queryKey(username: string, owner: AgentDiscussionOwner) {
  return [...DISCUSSION_QUERY, username, owner.project, owner.canvas, owner.conversation] as const;
}

function storageKey(identity: string): string {
  return `${SAVED_INTENT_PREFIX}${encodeURIComponent(identity)}`;
}

function isSavedIntent(value: unknown): value is SavedIntent {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<SavedIntent>;
  return typeof candidate.requestId === "string" &&
    (candidate.status === "pending" || candidate.status === "submission_unknown") &&
    typeof candidate.message === "string";
}

function readIntentStorage(identity: string): { value: SavedIntent[]; ok: boolean } {
  try {
    const raw = localStorage.getItem(storageKey(identity));
    if (!raw) return { value: [], ok: true };
    const value: unknown = JSON.parse(raw);
    if (Array.isArray(value)) return { value: value.filter(isSavedIntent), ok: true };
    // Read older single-intent entries written by a previous frontend build.
    return { value: isSavedIntent(value) ? [value] : [], ok: true };
  } catch {
    // A malformed or unavailable entry must not be used to replace stored state.
    return { value: [], ok: false };
  }
}

function writeIntents(identity: string, value: SavedIntent[]): boolean {
  try {
    if (value.length > 0) localStorage.setItem(storageKey(identity), JSON.stringify(value));
    else localStorage.removeItem(storageKey(identity));
    return true;
  } catch {
    return false;
  }
}

function applyIntentChanges(
  persisted: SavedIntent[],
  previous: SavedIntent[],
  next: SavedIntent[],
): SavedIntent[] {
  const nextIds = new Set(next.map((intent) => intent.requestId));
  const removedIds = new Set(
    previous
      .filter((intent) => !nextIds.has(intent.requestId))
      .map((intent) => intent.requestId),
  );
  const merged = persisted.filter((intent) => !removedIds.has(intent.requestId));

  for (const intent of next) {
    const existingIndex = merged.findIndex((item) => item.requestId === intent.requestId);
    if (existingIndex >= 0) merged[existingIndex] = intent;
    else merged.push(intent);
  }
  return merged;
}

function reconcileIntents(
  intents: SavedIntent[],
  messages: AgentDiscussionMessage[],
): SavedIntent[] {
  return intents.flatMap((intent) => {
    const requestMessages = messages.filter((item) => item.request_id === intent.requestId);
    if (requestMessages.some((item) => item.role === "assistant" && item.status === "completed")) {
      return [];
    }
    const userMessage = requestMessages.find((item) => item.role === "user");
    if (!userMessage) return [intent];
    return [{
      ...intent,
      status: userMessage.status === "submission_unknown" ? "submission_unknown" : "pending",
    }];
  });
}

function requestId(): string {
  return crypto.randomUUID();
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "讨论请求失败，请保留草稿后重试查看。";
}

export function useAgentDiscussion(
  owner: AgentDiscussionOwner,
  active = true,
): AgentDiscussionState {
  const queryClient = useQueryClient();
  const username = useAuthStore((state) => state.username);
  const identity = username ? identityKey(username, owner) : "";
  const key = useMemo(
    () => (username ? queryKey(username, owner) : [...DISCUSSION_QUERY, "signed-out"] as const),
    [username, owner.project, owner.canvas, owner.conversation],
  );
  const latest = useRef({ identity, active });
  latest.current = { identity, active };
  const mounted = useRef(true);
  const inFlight = useRef(new Set<string>());
  const intentsByIdentity = useRef(new Map<string, SavedIntent[]>());
  const getOwnerIntents = (ownerIdentity: string): SavedIntent[] => {
    const persisted = readIntentStorage(ownerIdentity);
    if (persisted.ok) {
      intentsByIdentity.current.set(ownerIdentity, persisted.value);
      return persisted.value;
    }
    const cached = intentsByIdentity.current.get(ownerIdentity);
    return cached ?? [];
  };
  const [intentState, setIntentState] = useState<{
    identity: string;
    value: SavedIntent[];
  }>(() => ({ identity, value: identity ? getOwnerIntents(identity) : [] }));
  const [busyOwners, setBusyOwners] = useState<Set<string>>(() => new Set());
  const [errorState, setErrorState] = useState({ identity, value: "" });

  const isCurrent = () =>
    mounted.current &&
    !!identity &&
    latest.current.identity === identity &&
    latest.current.active &&
    useAuthStore.getState().username === username;
  const rememberOwnerIntents = (ownerIdentity: string, value: SavedIntent[]) => {
    intentsByIdentity.current.set(ownerIdentity, value);
    setIntentState({ identity: ownerIdentity, value });
  };
  const setOwnerIntents = (
    ownerIdentity: string,
    value: SavedIntent[],
    requirePersistence = false,
  ): boolean => {
    const previous = intentsByIdentity.current.get(ownerIdentity) ?? [];
    const stored = readIntentStorage(ownerIdentity);
    if (!stored.ok) {
      if (!requirePersistence) {
        rememberOwnerIntents(ownerIdentity, applyIntentChanges(previous, previous, value));
      }
      return false;
    }

    const merged = applyIntentChanges(stored.value, previous, value);
    const persisted = writeIntents(ownerIdentity, merged);
    if (persisted || !requirePersistence) rememberOwnerIntents(ownerIdentity, merged);
    return persisted;
  };
  const setCurrentError = (value: string) => {
    if (isCurrent()) setErrorState({ identity, value });
  };
  const captureDiscussionCanvasBaseline = async (sourceRequestId: string): Promise<boolean> => {
    const runtime = captureFreezoneCanvasRuntime(owner.project, owner.canvas);
    if (!runtime || !runtime.isCurrent()) {
      setCurrentError("当前画布尚未就绪，协作请求未提交；请先打开并保存画布后重试。");
      return false;
    }
    try {
      if ((await runtime.flush()) !== true || !runtime.isCurrent() || !isCurrent()) {
        if (isCurrent()) setCurrentError("当前画布尚未保存，协作请求未提交；请处理同步状态后重试。");
        return false;
      }
      const serverBaseline = runtime.serverBaseline();
      if (!serverBaseline) {
        setCurrentError("无法确认当前画布的服务端基线，讨论未提交；请刷新画布后重试。");
        return false;
      }
      const refresh = await refreshAgentCanvasChanges(
        owner.project,
        owner.canvas,
        serverBaseline,
        runtime.isCurrent,
      );
      if (!runtime.isCurrent() || !isCurrent()) return false;
      if (refresh.status === "conflict") {
        const detail = refresh.paths.length ? `（${refresh.paths.join("、")}）` : "";
        const message = `协作提交前画布与服务端更新发生冲突${detail}；本地内容已保留，请先处理画布冲突。`;
        reportFreezoneCanvasConflict(owner.project, owner.canvas, message);
        setCurrentError(message);
        return false;
      }
      if (refresh.status !== "applied") {
        setCurrentError("协作提交前未能安全同步最新画布，讨论未提交；请刷新画布后重试。");
        return false;
      }
      const baseline = refresh.baseline;
      const runOwner: RunOwner = {
        user: username!,
        project: owner.project,
        canvas: owner.canvas,
        conversation: owner.conversation,
      };
      if (!saveDiscussionCanvasBaseline(runOwner, sourceRequestId, baseline)) {
        setCurrentError("无法保留协作前的完整画布基线，讨论未提交；请检查本地存储后重试。");
        return false;
      }
      return true;
    } catch {
      setCurrentError("无法确认当前画布已保存，协作请求未提交；请检查同步状态后重试。");
      return false;
    }
  };

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const historyQuery = useQuery({
    queryKey: key,
    queryFn: () => getAgentDiscussion(owner.project, {
      canvas_id: owner.canvas,
      conversation_id: owner.conversation,
    }),
    enabled: active && !!username,
    retry: false,
  });

  const savedIntents = intentState.identity === identity
    ? intentState.value
    : identity ? getOwnerIntents(identity) : [];

  useEffect(() => {
    if (!identity || !historyQuery.data) return;
    const currentIntents = getOwnerIntents(identity);
    const reconciled = reconcileIntents(currentIntents, historyQuery.data.messages);
    if (JSON.stringify(currentIntents) !== JSON.stringify(reconciled)) {
      setOwnerIntents(identity, reconciled);
    }
  // The latest `setOwnerIntents` closes over this render's identity.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identity, historyQuery.data]);

  async function refresh(): Promise<void> {
    if (!isCurrent() || !username) return;
    try {
      await queryClient.fetchQuery({
        queryKey: key,
        queryFn: () => getAgentDiscussion(owner.project, {
          canvas_id: owner.canvas,
          conversation_id: owner.conversation,
        }),
        staleTime: 0,
        retry: false,
      });
    } catch {
      // Keep cached history visible if a refresh fails.
    }
  }

  async function send(rawMessage: string, options: AgentDiscussionSendOptions = {}): Promise<boolean> {
    const normalizedMessage = rawMessage.trim();
    if (!isCurrent() || !username || inFlight.current.has(identity)) return false;
    if (!normalizedMessage || normalizedMessage.length > 12000) {
      setCurrentError("请输入 1 到 12000 个字符的讨论内容。");
      return false;
    }
    const storedIntents = getOwnerIntents(identity);
    const currentIntents = reconcileIntents(
      storedIntents,
      historyQuery.data?.messages ?? [],
    );
    if (JSON.stringify(currentIntents) !== JSON.stringify(storedIntents)) {
      if (!setOwnerIntents(identity, currentIntents)) {
        setCurrentError(INTENT_STORAGE_ERROR);
        return false;
      }
    }
    if (currentIntents.some((item) => item.message === normalizedMessage)) {
      await refresh();
      return false;
    }
    if (historyQuery.data?.availability.available !== true) {
      if (historyQuery.data?.availability.available === false) {
        setCurrentError("当前讨论模型暂不可用；草稿已保留。");
      }
      return false;
    }

    const id = requestId();
    inFlight.current.add(identity);
    setBusyOwners((previous) => new Set(previous).add(identity));
    setCurrentError("");
    let postActive = true;
    const finishPost = () => {
      if (!postActive) return;
      postActive = false;
      inFlight.current.delete(identity);
      setBusyOwners((previous) => {
        const next = new Set(previous);
        next.delete(identity);
        return next;
      });
    };
    if (!(await captureDiscussionCanvasBaseline(id)) || !isCurrent()) {
      finishPost();
      return false;
    }
    const intent: SavedIntent = {
      requestId: id,
      status: "pending",
      message: normalizedMessage,
    };
    const pendingIntents = [...currentIntents, intent];
    if (!setOwnerIntents(identity, pendingIntents, true)) {
      finishPost();
      setCurrentError(INTENT_STORAGE_ERROR);
      return false;
    }
    try {
      const receipt = await postAgentDiscussion(owner.project, {
        request_id: id,
        canvas_id: owner.canvas,
        conversation_id: owner.conversation,
        message: normalizedMessage,
        ...(options.targetAdoptionId ? { target_adoption_id: options.targetAdoptionId } : {}),
        ...(options.selection ? { selection: options.selection } : {}),
      });
      finishPost();
      const task = receipt.collaboration_task;
      if (task?.source_request_id === id && task.delegation_id) {
        associateDiscussionCanvasBaseline({
          user: username,
          project: owner.project,
          canvas: owner.canvas,
          conversation: owner.conversation,
        }, {
          sourceRequestId: id,
          taskId: task.id,
          canvasId: task.canvas_id,
          conversationId: task.conversation_id,
          delegationId: task.delegation_id,
          rootDelegationId: task.delegation_id,
          runId: task.run_id,
        });
      }
      if (useAuthStore.getState().username === username && latest.current.active) {
        void queryClient.invalidateQueries({
          queryKey: ["collaboration-tasks", username, owner.project, owner.canvas, owner.conversation],
        });
      }
      if (receipt.status === "completed") {
        setOwnerIntents(
          identity,
          getOwnerIntents(identity).filter((item) => item.requestId !== id),
        );
        if (useAuthStore.getState().username === username && latest.current.active) {
          // A completed receipt is durable; this GET only refreshes this owner's cache.
          void queryClient.fetchQuery({
            queryKey: key,
            queryFn: () => getAgentDiscussion(owner.project, {
              canvas_id: owner.canvas,
              conversation_id: owner.conversation,
            }),
            staleTime: 0,
            retry: false,
          }).catch(() => undefined);
        }
        return isCurrent();
      }

      const unresolved: SavedIntent = {
        ...intent,
        status: receipt.status === "submission_unknown" ? "submission_unknown" : "pending",
      };
      setOwnerIntents(identity, getOwnerIntents(identity).map((item) =>
        item.requestId === id ? unresolved : item,
      ));
      if (useAuthStore.getState().username === username && latest.current.active) {
        await queryClient.fetchQuery({
          queryKey: key,
          queryFn: () => getAgentDiscussion(owner.project, {
            canvas_id: owner.canvas,
            conversation_id: owner.conversation,
          }),
          staleTime: 0,
          retry: false,
        }).catch(() => undefined);
      }
      setCurrentError("讨论提交状态尚未确认；请刷新状态后再继续。");
      return false;
    } catch (error) {
      finishPost();
      const status = error instanceof ApiError
        ? error.status
        : error && typeof error === "object" &&
            typeof (error as { status?: unknown }).status === "number"
          ? (error as { status: number }).status
          : null;
      if (status === 422) {
        setOwnerIntents(
          identity,
          getOwnerIntents(identity).filter((item) => item.requestId !== id),
        );
      } else {
        setOwnerIntents(identity, getOwnerIntents(identity).map((item) =>
          item.requestId === id
            ? { ...item, status: "submission_unknown" }
            : item,
        ));
        if (useAuthStore.getState().username === username && latest.current.active) {
          await queryClient.fetchQuery({
            queryKey: key,
            queryFn: () => getAgentDiscussion(owner.project, {
              canvas_id: owner.canvas,
              conversation_id: owner.conversation,
            }),
            staleTime: 0,
            retry: false,
          }).catch(() => undefined);
        }
      }
      setCurrentError(errorText(error));
      return false;
    } finally {
      finishPost();
    }
  }

  const error = errorState.identity === identity
    ? errorState.value
    : "";

  return {
    messages: historyQuery.data?.messages ?? [],
    availability: historyQuery.data?.availability ?? null,
    loading: historyQuery.isLoading,
    busy: busyOwners.has(identity),
    error: error || (historyQuery.isError ? errorText(historyQuery.error) : ""),
    pendingRequests: savedIntents,
    pendingRequest: savedIntents[0] ?? null,
    send,
    refresh,
  };
}
