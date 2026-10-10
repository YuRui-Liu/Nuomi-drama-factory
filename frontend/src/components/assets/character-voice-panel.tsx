// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  AlertTriangle,
  Loader2,
  Mic,
  Scissors,
  Square,
  Trash2,
  Upload,
  Volume2,
  WandSparkles,
} from "lucide-react";

import {
  useCharacters,
  useCharacterVoiceSamples,
  useCharacterVoiceRecommendation,
  useRefineCharacterVoice,
  useDeleteCharacterVoiceSample,
  useDesignCharacterVoiceSample,
  useRecordCharacterVoiceSample,
  useTrimCharacterVoiceSample,
  useUploadCharacterVoiceSample,
  useUpdateCharacter,
  useApproveCharacterVoiceCandidate,
  useDeleteCharacterVoiceCandidate,
} from "@/lib/queries/characters";
import { resolveMediaUrl } from "@/lib/media-url";
import { backendErrorToastMessage } from "@/lib/api-errors";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { useTaskController } from "@/hooks/use-task-controller";
import { queryKeys } from "@/lib/query-keys";
import type { Character, CharacterVoiceDesign, CharacterVoiceFacts, CharacterVoiceSlot, CharacterVoiceSlotId } from "@/types/character";
import { CharacterVoiceDesignForm, voiceDesignPreview } from "./character-voice-design-form";
import { useProject } from "@/lib/queries/projects";
import { GlobalVoicePicker, VoiceAssetFavorite } from "./global-voice-assets";

type VoiceDraft = { description: string; audition: string; age: string; design: CharacterVoiceDesign | null; context?: Record<string, unknown>; edited: { description: boolean; audition: boolean } };
const voiceDrafts = new Map<string, VoiceDraft>();

const AGE_SLOT_ORDER: CharacterVoiceSlotId[] = ["child", "youth", "middle", "elder"];
const SUPPORTED_AUDIO_ACCEPT = ".mp3,.wav,.m4a,.aac,.ogg,audio/*";
const EMPTY_VOICE_SLOTS: CharacterVoiceSlot[] = [];

type VoiceRow = {
  displaySlot: CharacterVoiceSlotId;
  actionSlot: CharacterVoiceSlot;
  label: string;
};

function emptySlot(slot: CharacterVoiceSlotId, label: string): CharacterVoiceSlot {
  return {
    slot,
    label,
    path: "",
    url: "",
    sha256: "",
    updated_at: "",
    inherited_from_default: false,
    required: slot === "default",
  };
}

function dataUrlFromBlob(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

function isOkResponse<T>(res: unknown): res is { ok: true; data: T } {
  return Boolean(res && typeof res === "object" && (res as { ok?: unknown }).ok === true);
}

function isErrorResponse(res: unknown): res is { ok: false; error?: string } {
  return Boolean(res && typeof res === "object" && (res as { ok?: unknown }).ok === false);
}

// getUserMedia rejects with a named DOMException. Map the common ones to
// actionable copy so a recording failure says *why* (permission blocked, no
// device, device busy…) instead of an opaque「录音失败」. Note: a server
// `Permissions-Policy: microphone=()` header also surfaces here as
// NotAllowedError — see worker/index.ts.
function recordErrorMessageKey(error: unknown): string {
  const name = error instanceof DOMException ? error.name : "";
  switch (name) {
    case "NotAllowedError":
    case "SecurityError":
      return "characters.voiceSamples.recordPermissionDenied";
    case "NotFoundError":
    case "DevicesNotFoundError":
      return "characters.voiceSamples.recordNoDevice";
    case "NotReadableError":
    case "TrackStartError":
      return "characters.voiceSamples.recordDeviceBusy";
    default:
      return "characters.voiceSamples.recordFailed";
  }
}

function VoiceActionButton({
  label,
  icon,
  onClick,
  disabled,
  destructive = false,
}: {
  label: string;
  icon: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  destructive?: boolean;
}) {
  return (
    <Button
      type="button"
      size="icon-xs"
      variant="ghost"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      className={cn(
        "text-muted-foreground hover:text-foreground",
        destructive && "hover:bg-destructive/10 hover:text-destructive",
      )}
    >
      {icon}
    </Button>
  );
}

export function CharacterVoicePanel(props: { project: string; character: Character; active?: boolean }) {
  return <CharacterVoiceWorkspaces key={props.project} {...props} />;
}

function CharacterVoiceWorkspaces({ project, character, active = true }: {
  project: string; character: Character; active?: boolean;
}) {
  const characters = useCharacters(project).data?.data;
  const [visited, setVisited] = useState<Character[]>(() => active ? [character] : []);
  // Keep each opened voice workspace alive: its requests, draft and generation
  // lock belong to that character, not to whichever character is now selected.
  // A project change resets this collection via the parent's key.
  if (active && !visited.some(item => item.name === character.name)) {
    setVisited(previous => [...previous, character]);
  }
  return visited.map(previous => {
    const current = previous.name === character.name
      ? character
      : characters?.find(item => item.name === previous.name) ?? previous;
    if (characters && !characters.some(item => item.name === previous.name)) return null;
    const visible = active && current.name === character.name;
    return <div key={current.name} hidden={!visible}>
      <CharacterVoicePanelContent project={project} character={current} active={visible} />
    </div>;
  });
}

function CharacterVoicePanelContent({
  project,
  character,
  active = true,
}: {
  project: string;
  character: Character;
  active?: boolean;
}) {
  const { t } = useTranslation();
  const samples = useCharacterVoiceSamples(project, character.name);
  const projectConfig = useProject(project).data?.data;
  const contextKey = JSON.stringify(projectConfig);
  const uploadVoice = useUploadCharacterVoiceSample(project, character.name);
  const recordVoice = useRecordCharacterVoiceSample(project, character.name);
  const trimVoice = useTrimCharacterVoiceSample(project, character.name);
  const deleteVoice = useDeleteCharacterVoiceSample(project, character.name);
  const designVoice = useDesignCharacterVoiceSample(project, character.name);
  const updateCharacter = useUpdateCharacter(project, character.name);
  const refineVoice = useRefineCharacterVoice(project, character.name);
  const draftKey = JSON.stringify([project, character.name]);
  const savedDraft = useRef(voiceDrafts.get(draftKey)).current;
  const [design, setDesign] = useState<CharacterVoiceDesign | null>(savedDraft?.design ?? null);
  const [designContext, setDesignContext] = useState<Record<string, unknown> | undefined>(savedDraft?.context);
  const draftVersion = useRef(0);
  const audioContainer = useRef<HTMLElement>(null);
  const approveCandidate = useApproveCharacterVoiceCandidate(project, character.name);
  const deleteCandidate = useDeleteCharacterVoiceCandidate(project, character.name);
  const [candidateToDelete, setCandidateToDelete] = useState<string | null>(null);
  const [candidatesOpen, setCandidatesOpen] = useState<boolean | null>(null);
  const knownCandidates = useRef<string[] | null>(null);
  const voiceDesignScope = `character:${character.name}:voice:default`;
  const voiceDesignTask = useTaskController({
    key: {
      taskType: "character_voice_design",
      project,
      episode: 0,
      scope: voiceDesignScope,
    },
    invalidateKeys: [
      queryKeys.characterVoiceSamples(project, character.name),
      queryKeys.characters(project),
    ],
    showCompleteToast: false,
    onComplete: () => toast.success("声音候选已保存，请试听后采用为角色声音。"),
  });

  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadSlotRef = useRef<string>("default");
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startedAtRef = useRef<number>(0);

  // Stop the recorder and release the mic if we unmount mid-recording;
  // otherwise the MediaStream tracks stay live (mic indicator stays on).
  useEffect(() => {
    return () => {
      try {
        mediaRecorderRef.current?.stop();
      } catch {
        // recorder may already be inactive
      }
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    };
  }, []);

  const [recordSlot, setRecordSlot] = useState<CharacterVoiceSlot | null>(null);
  const [recording, setRecording] = useState(false);
  const [recordedDataUrl, setRecordedDataUrl] = useState("");
  const [recordedDuration, setRecordedDuration] = useState<number | null>(null);
  const [recordStatus, setRecordStatus] = useState("");
  const [trimSlot, setTrimSlot] = useState<CharacterVoiceSlot | null>(null);
  const [trimStart, setTrimStart] = useState("0");
  const [trimDuration, setTrimDuration] = useState("4");
  const [slotDurations, setSlotDurations] = useState<Record<string, number>>({});
  const voiceSamples = isOkResponse(samples.data) ? samples.data.data : undefined;
  useEffect(() => {
    if (!voiceSamples) return;
    const ids = (voiceSamples.candidates ?? []).map(candidate => candidate.candidate_id);
    if (knownCandidates.current && ids.some(id => !knownCandidates.current!.includes(id))) setCandidatesOpen(true);
    knownCandidates.current = ids;
  }, [voiceSamples]);
  const storedFacts = voiceSamples?.voice_facts ?? character.voice_facts;
  const hasSavedVoice = !!voiceSamples?.slots.some((slot) => slot.url)
    || !!voiceSamples?.candidates?.some((candidate) => candidate.url);
  const [wantsNewVoice, setWantsNewVoice] = useState(false);
  const recommendation = useCharacterVoiceRecommendation(project, character, false);
  const generationLock = useRef(false);
  const [preparingVoice, setPreparingVoice] = useState(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const [confirmedFacts, setConfirmedFacts] = useState<CharacterVoiceFacts | null>(null);
  const facts = confirmedFacts ?? (storedFacts?.provenance === "human" ? storedFacts : recommendation.data?.voice_facts ?? storedFacts);
  const mode = facts?.vocalization_mode ?? "unknown";
  const nonverbal = mode === "nonverbal" || mode === "none";
  const canGenerate = (mode === "dialogue" || mode === "both") && !facts?.conflicts?.length;
  const [factDraft, setFactDraft] = useState<CharacterVoiceFacts | null>(null);
  const [voiceDescription, setVoiceDescription] = useState(savedDraft?.description ?? "");
  const [perceivedAge, setPerceivedAge] = useState(savedDraft?.age ?? "");
  const [auditionText, setAuditionText] = useState(savedDraft?.audition ?? "");
  const liveVoiceInput = useRef({ voiceDescription, auditionText, perceivedAge, storedFacts, confirmedFacts, character, design });
  liveVoiceInput.current = { voiceDescription, auditionText, perceivedAge, storedFacts, confirmedFacts, character, design };
  const edited = useRef(savedDraft?.edited ?? { description: false, audition: false });
  const factsEditor = useRef<HTMLDetailsElement>(null);
  const moreSettings = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const current = recommendation.isError ? undefined : recommendation.data;
    if (!current) return;
    if (!edited.current.description) {
      setVoiceDescription(current.voice_description);
      setDesign(current.design ?? null);
      setDesignContext(current.design_context);
    }
    if (!edited.current.audition) setAuditionText(current.audition_text);
  }, [recommendation.data, recommendation.isError]);
  useEffect(() => {
    voiceDrafts.set(draftKey, { description: voiceDescription, audition: auditionText, age: perceivedAge, design, context: designContext, edited: { ...edited.current } });
    if (voiceDrafts.size > 100) voiceDrafts.delete(voiceDrafts.keys().next().value!);
  }, [draftKey, voiceDescription, auditionText, perceivedAge, design, designContext]);
  const preparedOnOpen = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (active && projectConfig && !samples.isLoading && samples.data?.ok && (!hasSavedVoice || wantsNewVoice) && !nonverbal && !edited.current.description && preparedOnOpen.current !== contextKey) {
      preparedOnOpen.current = contextKey;
      void recommendation.refetch({ cancelRefetch: false });
    }
  }, [active, projectConfig, contextKey, samples.isLoading, samples.data, hasSavedVoice, wantsNewVoice, nonverbal, recommendation.refetch]);
  useEffect(() => {
    const container = audioContainer.current;
    if (!active) {
      if (mediaRecorderRef.current?.state === "recording") mediaRecorderRef.current.stop();
      streamRef.current?.getTracks().forEach(track => track.stop());
      setRecording(false);
    }
    return () => { container?.querySelectorAll("audio").forEach(audio => audio.pause()); };
  }, [active]);
  const refineDesign = async (adjustment: string) => {
    if (refineVoice.isPending || generationLock.current) return;
    const version = draftVersion.current;
    try {
      const result = await refineVoice.mutateAsync({ adjustment, design: design ?? undefined, refresh: true });
      if (!mounted.current || version !== draftVersion.current) return;
      edited.current = { description: true, audition: true };
      setDesign(result.design ?? null);
      setDesignContext(result.design_context);
      setVoiceDescription(result.voice_description);
      setAuditionText(result.audition_text);
      if (!liveVoiceInput.current.confirmedFacts) setConfirmedFacts(result.voice_facts);
    } catch (error) { if (mounted.current) toast.error(backendErrorToastMessage(error, t)); }
  };
  const storedFactsKey = JSON.stringify(storedFacts);
  useEffect(() => { setConfirmedFacts(null); }, [storedFactsKey]);
  const [confirmations, setConfirmations] = useState<Record<string, boolean>>({});
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const draft = factDraft ?? facts ?? {};
  const sampleSlots = voiceSamples?.slots ?? EMPTY_VOICE_SLOTS;
  const loadFailed = samples.isError || isErrorResponse(samples.data);

  const ageLabel = (slot: CharacterVoiceSlotId) => {
    const key =
      slot === "child"
        ? "characters.ageGroups.child"
        : slot === "youth"
          ? "characters.ageGroups.young"
          : slot === "middle"
            ? "characters.ageGroups.middle"
            : "characters.ageGroups.elder";
    return t(key);
  };

  const rows = useMemo<VoiceRow[]>(() => {
    const bySlot = new Map(sampleSlots.map((slot) => [slot.slot, slot]));
    const getSlot = (slot: CharacterVoiceSlotId) =>
      bySlot.get(slot) ?? emptySlot(slot, slot === "default" ? t("characters.voiceSamples.defaultRequired") : ageLabel(slot));
    if (nonverbal) {
      return [getSlot("default"), ...sampleSlots.filter((slot) => slot.slot !== "default" && slot.path)].filter((slot) => mode !== "none" || slot.path).map((slot) => ({
        displaySlot: slot.slot as CharacterVoiceSlotId,
        actionSlot: slot,
        label: slot.slot === "default" ? "角色声音" : `已有音频（${slot.slot}）`,
      }));
    }
    const voiceAge = facts?.age_group || character.age_group;
    const primaryAge = AGE_SLOT_ORDER.includes(voiceAge as CharacterVoiceSlotId)
      ? (voiceAge as CharacterVoiceSlotId)
      : "";

    if (primaryAge) {
      return [primaryAge, ...AGE_SLOT_ORDER.filter((slot) => slot !== primaryAge)].map((slot) => {
        const actionSlot = slot === primaryAge ? getSlot("default") : getSlot(slot);
        return {
          displaySlot: slot,
          actionSlot,
          label:
            slot === primaryAge
              ? t("characters.voiceSamples.ageDefaultRequired", { age: ageLabel(slot) })
              : t("characters.voiceSamples.optionalOverride", { age: ageLabel(slot) }),
        };
      });
    }

    return [
      {
        displaySlot: "default" as const,
        actionSlot: getSlot("default"),
        label: t("characters.voiceSamples.defaultRequired"),
      },
      ...AGE_SLOT_ORDER.map((slot) => ({
        displaySlot: slot,
        actionSlot: getSlot(slot),
        label: t("characters.voiceSamples.optionalOverride", { age: ageLabel(slot) }),
      })),
    ];
  }, [character.age_group, facts?.age_group, sampleSlots, t, nonverbal, mode]);

  const pending =
    uploadVoice.isPending ||
    recordVoice.isPending ||
    trimVoice.isPending ||
    deleteVoice.isPending ||
    designVoice.isPending ||
    updateCharacter.isPending ||
    approveCandidate.isPending ||
    voiceDesignTask.started;

  const finishMutation = <T,>(res: unknown, successMessage: string): boolean => {
    if (isErrorResponse(res)) {
      toast.error(res.error || t("common.error"));
      return false;
    }
    if (isOkResponse<T>(res)) {
      toast.success(successMessage);
      return true;
    }
    toast.error(t("common.error"));
    return false;
  };

  const handleUploadFile = async (file: File) => {
    const slot = uploadSlotRef.current;
    try {
      const res = await uploadVoice.mutateAsync({ slot, file });
      finishMutation<CharacterVoiceSlot>(
        res,
        t("characters.voiceSamples.uploaded"),
      );
    } catch {
      toast.error(t("common.error"));
    }
  };

  const openRecord = (slot: CharacterVoiceSlot) => {
    setRecordSlot(slot);
    setRecordedDataUrl("");
    setRecordedDuration(null);
    setRecordStatus(t("characters.voiceSamples.recordReady"));
  };

  const openUpload = (slot: CharacterVoiceSlot) => {
    uploadSlotRef.current = slot.slot;
    fileInputRef.current?.click();
  };

  const designVoiceSample = async (slot: CharacterVoiceSlot) => {
    if (generationLock.current || pending || nonverbal || facts?.conflicts?.length) return;
    generationLock.current = true;
    setPreparingVoice(true);
    const sourceKey = JSON.stringify({ character, storedFacts });
    try {
      let description = voiceDescription.trim();
      let audition = auditionText.trim();
      let selectedDesign = design;
      let effectiveFacts = facts;
      if ((!description && !selectedDesign) || !audition) {
        const result = await recommendation.refetch();
        if (!mounted.current) return;
        if (result.isError || !result.data) throw result.error ?? new Error("声音建议生成失败，请重试");
        const live = liveVoiceInput.current;
        if (JSON.stringify({ character: live.character, storedFacts: live.storedFacts }) !== sourceKey) {
          toast.error("角色资料已变化，请重新点击生成"); return;
        }
        description = edited.current.description ? live.voiceDescription.trim() : result.data.voice_description;
        selectedDesign = edited.current.description ? live.design : result.data.design ?? null;
        audition = edited.current.audition ? live.auditionText.trim() : result.data.audition_text;
        effectiveFacts = live.confirmedFacts ?? (live.storedFacts?.provenance === "human" ? live.storedFacts : result.data.voice_facts);
      }
      if (!mounted.current) return;
      if ((!description && !selectedDesign) || !audition) { toast.error("请补充声音描述和试听文本"); return; }
      if (!["dialogue", "both"].includes(effectiveFacts?.vocalization_mode ?? "unknown") || effectiveFacts?.conflicts?.length) {
        toast.error("请先确认角色需要人声对白，再点击生成"); return;
      }
      const res = await designVoice.mutateAsync({
        slot: String(slot.slot),
        ...(selectedDesign ? { design: { ...selectedDesign, audition_text: audition, audition_source: edited.current.audition ? "custom" : selectedDesign.audition_source } } : { voice_description: description }),
        perceived_age: liveVoiceInput.current.perceivedAge.trim(),
        audition_text: audition,
      });
      if (isErrorResponse(res)) {
        toast.error(res.error || t("common.error"));
        return;
      }
      voiceDesignTask.start({ scope: res.scope });
      toast.success("声音候选生成已排队，完成后需试听确认。" );
    } catch (error) {
      toast.error(backendErrorToastMessage(error, t));
    } finally {
      generationLock.current = false;
      if (mounted.current) setPreparingVoice(false);
    }
  };

  const confirmVocalization = async (vocalization_mode: "dialogue" | "nonverbal" | "none" | "both") => {
    const next: CharacterVoiceFacts = { ...facts, vocalization_mode, provenance: "human",
      conflicts: (facts?.conflicts ?? []).filter((issue) => !issue.startsWith("vocalization_mode:")) };
    try {
      const response = await updateCharacter.mutateAsync({ voice_facts: next });
      if (finishMutation(response, "已保存发声方式")) setConfirmedFacts(next);
    } catch { toast.error("发声方式保存失败，请重试"); }
  };

  const openFacts = () => {
    if (moreSettings.current) moreSettings.current.open = true;
    if (factsEditor.current) { factsEditor.current.open = true; factsEditor.current.scrollIntoView?.({ block: "nearest" }); }
  };

  const stopRecorderTracks = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  };

  const startRecording = async () => {
    if (!recordSlot) return;
    // Non-secure origins (plain http over a LAN IP, etc.) get no microphone at
    // all — browsers only expose getUserMedia on HTTPS or localhost. Say so
    // explicitly instead of falling through to a generic failure.
    if (!window.isSecureContext) {
      toast.error(t("characters.voiceSamples.recordInsecureContext"));
      setRecordStatus(t("characters.voiceSamples.recordInsecureContext"));
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      toast.error(t("characters.voiceSamples.recordUnavailable"));
      return;
    }

    try {
      setRecordedDataUrl("");
      setRecordedDuration(null);
      setRecordStatus(t("characters.voiceSamples.requestingMic"));
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      chunksRef.current = [];
      const recorder = new MediaRecorder(stream);
      mediaRecorderRef.current = recorder;
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = async () => {
        try {
          const mimeType = recorder.mimeType || "audio/webm";
          const blob = new Blob(chunksRef.current, { type: mimeType });
          const dataUrl = await dataUrlFromBlob(blob);
          const duration = Math.max(0, (performance.now() - startedAtRef.current) / 1000);
          setRecordedDataUrl(dataUrl);
          setRecordedDuration(duration);
          setRecordStatus(
            t("characters.voiceSamples.recordedDuration", {
              seconds: duration.toFixed(1),
            }),
          );
        } catch {
          setRecordStatus(t("characters.voiceSamples.recordFailed"));
        } finally {
          setRecording(false);
          stopRecorderTracks();
        }
      };
      startedAtRef.current = performance.now();
      recorder.start();
      setRecording(true);
      setRecordStatus(t("characters.voiceSamples.recording"));
    } catch (error) {
      stopRecorderTracks();
      setRecording(false);
      // Don't swallow the rejection — log it and map the DOMException name to a
      // specific reason. (A `Permissions-Policy: microphone=()` server header
      // shows up here as NotAllowedError; fixed in worker/index.ts.)
      console.error("[voice-record] getUserMedia failed", error);
      const messageKey = recordErrorMessageKey(error);
      toast.error(t(messageKey));
      setRecordStatus(t(messageKey));
    }
  };

  const stopRecording = () => {
    mediaRecorderRef.current?.stop();
  };

  const saveRecording = async () => {
    if (!recordSlot || !recordedDataUrl) return;
    try {
      const res = await recordVoice.mutateAsync({
        slot: String(recordSlot.slot),
        dataUrl: recordedDataUrl,
      });
      if (finishMutation<CharacterVoiceSlot>(res, t("characters.voiceSamples.recorded"))) {
        setRecordSlot(null);
      }
    } catch {
      toast.error(t("common.error"));
    }
  };

  const closeRecordDialog = (open: boolean) => {
    if (open) return;
    if (recording) {
      mediaRecorderRef.current?.stop();
    } else {
      stopRecorderTracks();
    }
    setRecordSlot(null);
  };

  const openTrim = (slot: CharacterVoiceSlot) => {
    setTrimSlot(slot);
    setTrimStart("0");
    setTrimDuration("4");
  };

  const applyTrim = async () => {
    if (!trimSlot?.path) return;
    const startSeconds = Number(trimStart);
    const durationSeconds = Number(trimDuration);
    if (!Number.isFinite(startSeconds) || !Number.isFinite(durationSeconds)) {
      toast.error(t("characters.voiceSamples.invalidTrim"));
      return;
    }
    try {
      const res = await trimVoice.mutateAsync({
        slot: String(trimSlot.slot),
        sourcePath: trimSlot.path,
        startSeconds,
        durationSeconds,
      });
      if (finishMutation<CharacterVoiceSlot>(res, t("characters.voiceSamples.trimmed"))) {
        setTrimSlot(null);
      }
    } catch {
      toast.error(t("common.error"));
    }
  };

  const clearSlot = async (slot: CharacterVoiceSlot) => {
    try {
      const res = await deleteVoice.mutateAsync(String(slot.slot));
      finishMutation<CharacterVoiceSlot>(res, t("characters.voiceSamples.cleared"));
    } catch {
      toast.error(t("common.error"));
    }
  };

  const renderVoiceRow = ({ displaySlot, actionSlot, label }: VoiceRow) => {
            const hasVoice = Boolean(actionSlot.path);
            const audioSrc = resolveMediaUrl(actionSlot.url);
            const slotId = String(actionSlot.slot);
            const duration = slotDurations[slotId];
            return (
              <div
                key={`${displaySlot}:${slotId}`}
                data-voice-slot={slotId}
                className="flex flex-wrap items-center gap-3 py-3 first:pt-2 last:pb-0"
              >
                <div className="flex min-w-0 items-center gap-2 @[860px]:w-52 @[860px]:shrink-0">
                  <span
                    className={cn(
                      "inline-flex size-2 rounded-full",
                      hasVoice
                        ? "bg-emerald-400"
                        : actionSlot.required
                          ? "bg-amber-400"
                          : "bg-muted-foreground/40",
                    )}
                  />
                  <span className="truncate text-xs font-medium text-foreground">
                    {label}
                  </span>
                </div>

                <div className="flex min-w-0 flex-1 items-center gap-3">
                  {hasVoice ? (
                    <>
                      {audioSrc && (
                        <audio
                          src={audioSrc}
                          controls
                          className="h-6 max-w-[220px] shrink-0"
                          onLoadedMetadata={(event) => {
                            const nextDuration = event.currentTarget.duration;
                            if (Number.isFinite(nextDuration)) {
                              setSlotDurations((prev) => ({
                                ...prev,
                                [slotId]: nextDuration,
                              }));
                            }
                          }}
                        />
                      )}
                      <span className="truncate text-[11px] text-muted-foreground/70" title={actionSlot.path}>
                        {actionSlot.path.split("/").pop()}
                      </span>
                      {Number.isFinite(duration) && (
                        <span className="shrink-0 text-[11px] text-muted-foreground/70">
                          {t("characters.voiceSamples.currentDuration", {
                            seconds: duration.toFixed(1),
                          })}
                        </span>
                      )}
                    </>
                  ) : actionSlot.required ? (
                    <p className="flex items-center gap-1.5 text-[11px] text-amber-700 dark:text-amber-300">
                      <AlertTriangle className="size-3.5" />
                      尚未设置声音，试听满意后即可采用
                    </p>
                  ) : actionSlot.inherited_from_default ? (
                    <p className="text-[11px] italic text-muted-foreground">
                      {t("characters.voiceSamples.inheritedDefault")}
                    </p>
                  ) : (
                    <p className="text-[11px] text-muted-foreground">
                      {t("characters.voiceSamples.missing")}
                    </p>
                  )}
                </div>

                <details className="shrink-0"><summary className="cursor-pointer text-xs text-muted-foreground">管理音频</summary><div className="mt-2 flex items-center gap-1">
                  <GlobalVoicePicker project={project} character={character.name} slot={slotId} reference={{ path: actionSlot.inherited_from_default ? "" : actionSlot.path || "", sha256: actionSlot.inherited_from_default ? "" : actionSlot.sha256 || "" }} />
                  <VoiceActionButton
                    label={t("characters.voiceSamples.upload")}
                    icon={<Upload className="size-3.5" />}
                    disabled={pending}
                    onClick={() => {
                      uploadSlotRef.current = slotId;
                      fileInputRef.current?.click();
                    }}
                  />
                  <VoiceActionButton
                    label={t("characters.voiceSamples.record")}
                    icon={<Mic className="size-3.5" />}
                    disabled={pending}
                    onClick={() => openRecord(actionSlot)}
                  />
                  {!nonverbal && actionSlot.slot !== "default" && canGenerate && (
                    <VoiceActionButton
                      label={t("characters.voiceSamples.design")}
                      icon={<WandSparkles className="size-3.5" />}
                      disabled={pending || !canGenerate || samples.isLoading || loadFailed}
                      onClick={() => void designVoiceSample(actionSlot)}
                    />
                  )}
                  {hasVoice && (
                    <>
                      <VoiceActionButton
                        label={t("characters.voiceSamples.trim")}
                        icon={<Scissors className="size-3.5" />}
                        disabled={pending}
                        onClick={() => openTrim(actionSlot)}
                      />
                      <VoiceActionButton
                        label={t("characters.voiceSamples.clear")}
                        icon={<Trash2 className="size-3.5" />}
                        disabled={pending}
                        destructive
                        onClick={() => clearSlot(actionSlot)}
                      />
                    </>
                  )}
                </div></details>
              </div>
            );
  };

  return (
    <section ref={audioContainer} onPlayCapture={event => { audioContainer.current?.querySelectorAll("audio").forEach(audio => { if (audio !== event.target) audio.pause(); }); }} className="rounded-[10px] border border-white/[0.06] bg-white/[0.018] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3 pb-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-foreground">
            角色声音
          </h3>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-muted-foreground">
            为当前角色生成声音，试听满意后采用。采用前会保留当前声音。
          </p>
        </div>
        {pending && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
      </div>

      <h4 className="mt-2 text-sm font-medium">当前声音</h4>
      {nonverbal && <div className="my-3 space-y-3 rounded-lg border border-border bg-muted/30 p-4 text-sm">
        <p>{mode === "none" ? "当前设定为不发声。" : "当前设定为非语言发声，因此没有开启对白声线设计。"}动物或幻想角色也可以由你指定使用拟人化对白。</p>
        <div className="flex flex-wrap gap-2"><Button size="sm" disabled={pending} onClick={() => void confirmVocalization("dialogue")}>启用拟人化对白</Button><Button size="sm" variant="outline" disabled={pending} onClick={() => void confirmVocalization("both")}>对白与叫声都需要</Button><Button size="sm" variant="outline" disabled={pending} onClick={() => openUpload(sampleSlots.find(slot => slot.slot === "default") ?? emptySlot("default", "角色声音"))}>上传叫声或音效</Button><Button size="sm" variant="ghost" disabled={pending} onClick={() => openRecord(sampleSlots.find(slot => slot.slot === "default") ?? emptySlot("default", "角色声音"))}>录制声音</Button></div>
        <p className="text-xs text-muted-foreground">启用对白会保存你的发声方式选择；纯叫声可上传或录制，当前 Qwen3 声线工作流用于人声对白。</p>
      </div>}
      {voiceDesignTask.started && <p role="status" className="my-2 text-xs text-muted-foreground">{voiceDesignTask.stream.currentTask || "正在生成试听候选…"} · {Math.round(voiceDesignTask.stream.progress * 100)}%</p>}
      {voiceDesignTask.stream.error && <p role="alert" className="my-2 text-xs text-destructive">{voiceDesignTask.stream.error}。声音方案与当前音频已保留。</p>}
      {mode === "none" && <p className="mt-2 text-xs text-muted-foreground">无需配音，已有音频仍可试听和管理。</p>}
      {samples.isLoading ? (
        <p className="mt-4 text-xs text-muted-foreground">
          {t("characters.voiceSamples.loading")}
        </p>
      ) : loadFailed ? (
        <p className="mt-4 rounded-[8px] border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">
          {t("characters.voiceSamples.loadFailed")}
        </p>
      ) : (
        <div className="mt-3 divide-y divide-white/[0.06]">
          {rows.filter((row) => row.actionSlot.slot === "default").map(renderVoiceRow)}
        </div>
      )}

      {!nonverbal && hasSavedVoice && !wantsNewVoice && <div className="mt-4 space-y-2 text-xs text-muted-foreground"><p>已有声音可直接试听使用，不会自动重新生成。</p><Button size="sm" variant="outline" onClick={() => setWantsNewVoice(true)}>设计新的声音</Button></div>}
      {!nonverbal && (!hasSavedVoice || wantsNewVoice) && <div className="mt-4 space-y-3 rounded-lg border border-border bg-card/40 p-4">
        <h4 className="flex items-center gap-2 text-sm font-medium"><WandSparkles className="size-4 text-primary" />为这个角色推荐的声音</h4>
        {recommendation.isFetching && <p role="status" className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="size-3 animate-spin" />正在阅读角色资料，准备声音建议与试听台词…</p>}
        {recommendation.isError && <div className="text-xs text-muted-foreground">暂时无法获取 AI 推荐，可重试或自行调整声音。<Button size="sm" variant="ghost" onClick={() => void recommendation.refetch()}>重试推荐</Button></div>}
        <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" disabled={refineVoice.isPending || preparingVoice || recommendation.isFetching} onClick={() => void refineDesign("根据当前人物与影视风格重新拟定完整声音方案")}>{refineVoice.isPending ? "正在拟定…" : "重新拟定声音方案"}</Button><Button size="sm" variant="ghost" onClick={() => { edited.current.description = true; setDesign({ voice_spec: {}, performance: {} }); draftVersion.current++; }}>手动设计</Button></div>
        {design ? <CharacterVoiceDesignForm character={character} design={design} context={designContext} busy={refineVoice.isPending || preparingVoice} onRefine={text => void refineDesign(text)} onChange={next => { edited.current.description = true; draftVersion.current++; setDesign(next); setVoiceDescription(voiceDesignPreview(next)); }} /> : <p className="text-sm leading-6">{voiceDescription || "正在准备人物声音建议，也可手动填写描述后生成。"}</p>}
        {recommendation.data && <p className="text-xs text-muted-foreground">AI 创作建议，可调整，不改变角色原文事实。</p>}
        <div className="rounded-md bg-muted/40 p-3"><p className="mb-1 text-xs text-muted-foreground">{edited.current.audition ? "自定义试听台词" : recommendation.data?.audition_source === "dialogue" ? "角色原文对白" : "试听例句（非原文台词）"}</p><p className="text-sm leading-6">{auditionText || "推荐完成后自动准备，也可在“调整声音”中填写。"}</p></div>
        <details open className="text-xs"><summary className="cursor-pointer text-muted-foreground">试听文本与声音描述</summary>
        <div className="mt-3 grid grid-cols-1 gap-3 @md:grid-cols-2 text-xs">
          <label className="block space-y-1">自定义声音描述（编辑后切换为自由描述）<textarea className="min-h-28 w-full rounded-md border bg-background p-3 text-sm" aria-label="自定义声音描述" placeholder="描述听感年龄、音色、节奏和表演特点" value={voiceDescription} onChange={(event) => { edited.current.description = true; draftVersion.current++; setDesign(null); setVoiceDescription(event.target.value); }} /></label>
          <label className="block space-y-1">试听文本<textarea className="min-h-28 w-full rounded-md border bg-background p-3 text-sm" aria-label="试听文本" placeholder="输入适合这个角色的一句台词" value={auditionText} onChange={(event) => { edited.current.audition = true; draftVersion.current++; setAuditionText(event.target.value); }} /></label>
        </div>
        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground">高级声音参数</summary>
          <label className="mt-3 block space-y-1">听感年龄<Input aria-label="听感年龄" placeholder="留空沿用方案年龄，例如：老年" value={perceivedAge} onChange={(event) => { draftVersion.current++; setPerceivedAge(event.target.value); }} /></label>
          <p className="mt-2 text-muted-foreground">听感年龄仅用于声音设计，不修改原文事实；生成指定年龄槽位时，以槽位年龄为准。</p>
        </details>
        </details>
        {mode === "unknown" && !!voiceSamples && !recommendation.isFetching && <div className="space-y-2 rounded-md bg-muted/40 p-3 text-xs"><p>资料中尚未确认：这个角色需要人声对白吗？</p><div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" disabled={pending} onClick={() => void confirmVocalization("dialogue")}>需要人声对白</Button><Button size="sm" variant="ghost" disabled={pending} onClick={() => void confirmVocalization("nonverbal")}>只发出叫声等声音</Button><Button size="sm" variant="ghost" disabled={pending} onClick={() => void confirmVocalization("none")}>不需要发声</Button></div></div>}
        {!!facts?.conflicts?.length && <div role="alert" className="text-xs text-amber-500">声音资料有矛盾，需要先确认：{facts.conflicts.join("；")}<Button size="sm" variant="ghost" onClick={openFacts}>查看并修正</Button></div>}
        <Button size="sm" disabled={pending || preparingVoice || refineVoice.isPending || !!facts?.conflicts?.length || samples.isLoading || loadFailed || recommendation.isFetching} onClick={() => void designVoiceSample(sampleSlots.find((slot) => slot.slot === "default") ?? emptySlot("default", "角色声音"))}>
          <WandSparkles className="size-4" />{preparingVoice ? "正在准备并生成…" : "一键生成声音"}
        </Button>
        <p className="text-xs text-muted-foreground">仅在点击后生成音频，试听采用前保留当前声音。</p>
      </div>}

      {!!voiceSamples?.candidates?.length && <details open={candidatesOpen ?? !sampleSlots.some(slot => slot.path)} onToggle={event => setCandidatesOpen(event.currentTarget.open)} className="mt-4 space-y-3 rounded-lg border border-border p-4">
        <summary className="cursor-pointer text-sm font-medium">候选声音（{voiceSamples.candidates.length}）<span className="ml-2 text-xs text-muted-foreground">试听、恢复或删除</span></summary>
        {voiceSamples.candidates.map((candidate) => <div key={candidate.candidate_id} className="space-y-2 rounded-lg border p-3 text-xs">
          <p>{candidate.slot} · {sampleSlots.some(slot => slot.path === candidate.path && slot.sha256 === candidate.sha256) ? "当前采用" : candidate.status === "approved" ? "历史采用" : "试听候选"} {candidate.created_at}</p>
          {(candidate.status === "qc_unavailable" || candidate.report?.status === "qc_unavailable") && <p>质检不可用，自动质检尚未判定合格</p>}
          {candidate.url && <audio controls src={resolveMediaUrl(candidate.url) ?? undefined} />}
          <p>{candidate.report?.reason}</p>
          {candidate.status === "approved" && candidate.kind !== "nonverbal" && <VoiceAssetFavorite project={project} character={character.name} candidateId={candidate.candidate_id} description={candidate.payload?.voice_description || ""} suggestedTags={{ timbre: [candidate.payload?.design?.voice_spec.texture, candidate.payload?.design?.voice_spec.pitch, candidate.payload?.design?.voice_spec.resonance].filter((value): value is string => Boolean(value && value.length <= 30)) }} />}
          <details><summary>实际生成描述与参数</summary><p>{candidate.payload?.voice_description || "未提供描述"}</p><pre className="whitespace-pre-wrap">{JSON.stringify(candidate.payload, null, 2)}</pre></details>
          <label className="block"><input type="checkbox" checked={confirmations[candidate.candidate_id] ?? false} onChange={(event) => setConfirmations({ ...confirmations, [candidate.candidate_id]: event.target.checked })} /> 我已试听，确认适合此角色</label>
          <details><summary className="cursor-pointer text-muted-foreground">补充采用说明（可选）</summary><Input aria-label="采用理由" placeholder="可选，记录喜欢或需要改进的地方" value={reasons[candidate.candidate_id] ?? ""} onChange={(event) => setReasons({ ...reasons, [candidate.candidate_id]: event.target.value })} /></details>
          <Button size="sm" disabled={pending || !candidate.url || !confirmations[candidate.candidate_id] || sampleSlots.some(slot => slot.path === candidate.path && slot.sha256 === candidate.sha256)} onClick={async () => {
            try {
              const selectedSlot = sampleSlots.find(slot => slot.slot === candidate.slot);
              const res = await approveCandidate.mutateAsync({ candidateId: candidate.candidate_id, reason: reasons[candidate.candidate_id]?.trim() || "已试听，确认声音适合当前角色。", expectedReference: { path: selectedSlot?.inherited_from_default ? "" : selectedSlot?.path || "", sha256: selectedSlot?.inherited_from_default ? "" : selectedSlot?.sha256 || "" } });
              if (finishMutation(res, "候选已采用")) { setConfirmations({ ...confirmations, [candidate.candidate_id]: false }); setCandidatesOpen(false); setWantsNewVoice(false); audioContainer.current?.querySelectorAll("audio").forEach(audio => audio.pause()); }
            } catch (error) { toast.error(backendErrorToastMessage(error, t)); }
          }}>{candidate.status === "approved" ? "恢复为角色声音" : "采用为角色声音"}</Button>
          <Button size="sm" variant="ghost" disabled={pending || deleteCandidate.isPending} onClick={() => setCandidateToDelete(candidate.candidate_id)}><Trash2 className="size-3.5" />删除音频</Button>
        </div>)}
      </details>}

      <Dialog open={candidateToDelete !== null} onOpenChange={open => { if (!open && !deleteCandidate.isPending) setCandidateToDelete(null); }}>
        <DialogContent><DialogHeader><DialogTitle>删除这条角色音频？</DialogTitle></DialogHeader>
          <p className="text-sm text-muted-foreground">将从角色音频库移除；如果正在采用，也会解除绑定。已有成片不变，制作历史引用的文件保留。</p>
          <DialogFooter><Button variant="outline" disabled={deleteCandidate.isPending} onClick={() => setCandidateToDelete(null)}>取消</Button><Button variant="destructive" disabled={deleteCandidate.isPending} onClick={async () => {
            if (!candidateToDelete) return;
            try { const result = await deleteCandidate.mutateAsync(candidateToDelete); if (finishMutation(result, "音频已从角色音频库删除")) setCandidateToDelete(null); }
            catch (error) { toast.error(backendErrorToastMessage(error, t)); }
          }}>{deleteCandidate.isPending ? "正在删除…" : "删除音频"}</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <details ref={moreSettings} className="mt-4 rounded-lg border border-border p-4"><summary className="cursor-pointer text-sm text-muted-foreground">更多设置</summary>
      {!samples.isLoading && !loadFailed && rows.some((row) => row.actionSlot.slot !== "default") && <details className="mt-4 rounded-lg border border-border p-4">
        <summary className="cursor-pointer text-xs font-medium">{nonverbal ? "其他已有音频" : "不同年龄的声音（可选）"}</summary>
        <div className="mt-2 divide-y divide-white/[0.06]">{rows.filter((row) => row.actionSlot.slot !== "default").map(renderVoiceRow)}</div>
      </details>}

      <details ref={factsEditor} className="mt-4 space-y-4 rounded-lg border border-border bg-card/40 p-4 text-sm">
        <summary className="cursor-pointer text-xs font-medium">声音设定与依据</summary>
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full bg-muted px-3 py-1 text-xs">{{ source: "原文已记载", human: "已人工核对", unknown: "尚未核对" }[facts?.provenance ?? "unknown"]}</span>
          <span className="font-medium">{{ dialogue: "人声对白", nonverbal: "非语言发声", both: "对白与非语言发声", none: "不发声", unknown: "发声方式待核对" }[mode]}</span>
          {facts?.species && <span className="text-muted-foreground">· {facts.species}</span>}
          {facts?.age_group && <span className="text-muted-foreground">· {AGE_SLOT_ORDER.includes(facts.age_group as CharacterVoiceSlotId) ? ageLabel(facts.age_group as CharacterVoiceSlotId) : facts.age_group}</span>}
        </div>
        {!!facts?.evidence?.length && <details className="text-xs leading-6 text-muted-foreground"><summary className="cursor-pointer">查看原文依据</summary><ul className="mt-2 space-y-1">{facts.evidence.map((quote, index) => <li key={index}>{quote}</li>)}</ul></details>}
        {recommendation.data?.rationale && <p className="text-xs text-muted-foreground">AI 推荐说明：{recommendation.data.rationale}</p>}
        {mode === "unknown" && <div className="space-y-1 rounded-lg bg-amber-500/10 p-3 text-xs leading-5"><p className="text-muted-foreground">旧项目中的外观年龄不会自动当作声音事实。已有音频仍保留，可在上方试听。</p></div>}
        {nonverbal && !!facts?.conflicts?.length && <p role="alert" className="text-xs text-amber-500">发声事实存在冲突，请核对后保存：{facts.conflicts.join("；")}</p>}
        {nonverbal && <p className="rounded-lg bg-muted/50 p-3 text-sm leading-6">{mode === "none" ? "此角色设为不发声，无需配置人类年龄声线。" : "此角色使用叫声、呼噜等非语言声音，无需配置人类年龄声线。可在当前角色上传或录制音频，暂不支持用人声生成叫声。"}</p>}
        <details open={mode === "unknown" || !!facts?.conflicts?.length || !!factDraft} className="rounded-lg border border-border p-3">
          <summary className="cursor-pointer text-xs font-medium">核对或修改声音设定</summary>
          <div className="mt-4 grid grid-cols-1 gap-4 @md:grid-cols-2">
        <label className="block space-y-1 text-xs">发声模式
          <select aria-label="发声模式" value={draft.vocalization_mode ?? "unknown"} disabled={pending} onChange={(event) => setFactDraft({ ...draft, vocalization_mode: event.target.value as CharacterVoiceFacts["vocalization_mode"] })} className="ml-2 rounded border bg-background p-1">
            <option value="unknown">未知，待核对</option><option value="dialogue">对白</option><option value="nonverbal">非语言发声</option><option value="both">对白与非语言发声</option><option value="none">不发声</option>
          </select>
        </label>
        <label className="block space-y-1 text-xs">事实年龄<Input aria-label="事实年龄" placeholder="原文未说明可留空" value={draft.age_group ?? ""} onChange={(event) => setFactDraft({ ...draft, age_group: event.target.value })} /></label>
        <label className="block space-y-1 text-xs">物种<Input aria-label="物种" placeholder="按原文填写，不确定可留空" value={draft.species ?? ""} onChange={(event) => setFactDraft({ ...draft, species: event.target.value })} /></label>
        <label className="block space-y-1 text-xs">声音特征<Input aria-label="声音特征" placeholder="仅填写已有依据的特征" value={draft.voice_traits ?? ""} onChange={(event) => setFactDraft({ ...draft, voice_traits: event.target.value })} /></label>
          </div>
        <div className="mt-4">
        <Button size="sm" disabled={pending || !factDraft} onClick={async () => {
          try {
            const res = await updateCharacter.mutateAsync({ voice_facts: { ...draft, conflicts: [] } });
            if (finishMutation(res, "发声事实已人工确认")) setFactDraft(null);
          } catch { toast.error(t("common.error")); }
        }}>保存已核对的事实</Button>
        </div>
        </details>

      </details>
      </details>

      <input
        ref={fileInputRef}
        type="file"
        className="hidden"
        accept={SUPPORTED_AUDIO_ACCEPT}
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void handleUploadFile(file);
          event.target.value = "";
        }}
      />

      <Dialog open={Boolean(recordSlot)} onOpenChange={closeRecordDialog}>
        <DialogContent className="gap-4 rounded-2xl border border-white/[0.08] bg-zinc-950/30 p-5 shadow-2xl shadow-black/40 backdrop-blur-2xl sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="text-lg font-medium tracking-tight text-foreground">
              {t("characters.voiceSamples.recordTitle", {
                slot: recordSlot?.label ?? "",
              })}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <p className="text-sm leading-5 text-muted-foreground/80">
              {t("characters.voiceSamples.recordHint")}
            </p>
            <div className="rounded-[10px] border border-white/[0.08] bg-white/[0.04] p-3 text-sm text-muted-foreground/76">
              {recordStatus}
            </div>
            {recordedDataUrl && (
              <audio src={recordedDataUrl} controls className="h-9 w-full" />
            )}
            {recordedDuration !== null && (
              <p className="text-xs text-muted-foreground/65">
                {t("characters.voiceSamples.recordedDuration", {
                  seconds: recordedDuration.toFixed(1),
                })}
              </p>
            )}
          </div>
          <DialogFooter className="mt-2 flex flex-col gap-2 sm:flex-row sm:justify-end sm:gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={startRecording}
              disabled={recording || recordVoice.isPending}
              className="h-9 rounded-md border-white/[0.10] bg-white/[0.04] px-4 text-sm font-normal text-foreground/80 shadow-none hover:border-white/[0.18] hover:bg-white/[0.07] hover:text-foreground"
            >
              <Mic className="size-4" />
              {t("characters.voiceSamples.startRecord")}
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={stopRecording}
              disabled={!recording}
              className="h-9 rounded-md border-white/[0.10] bg-white/[0.04] px-4 text-sm font-normal text-foreground/80 shadow-none hover:border-white/[0.18] hover:bg-white/[0.07] hover:text-foreground"
            >
              <Square className="size-4" />
              {t("characters.voiceSamples.stopRecord")}
            </Button>
            <Button
              type="button"
              onClick={saveRecording}
              disabled={!recordedDataUrl || recordVoice.isPending}
              className="h-9 rounded-md bg-primary px-4 text-sm font-normal text-primary-foreground shadow-lg shadow-primary/15 hover:bg-primary/90"
            >
              {recordVoice.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Volume2 className="size-4" />
              )}
              {t("characters.voiceSamples.saveRecording")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(trimSlot)} onOpenChange={(open) => !open && setTrimSlot(null)}>
        <DialogContent className="gap-4 rounded-2xl border border-white/[0.08] bg-zinc-950/30 p-5 shadow-2xl shadow-black/40 backdrop-blur-2xl sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="text-lg font-medium tracking-tight text-foreground">
              {t("characters.voiceSamples.trimTitle", {
                slot: trimSlot?.label ?? "",
              })}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <p className="text-sm leading-5 text-muted-foreground/80">
              {t("characters.voiceSamples.trimHint")}
            </p>
            {trimSlot?.url && resolveMediaUrl(trimSlot.url) && (
              <audio src={resolveMediaUrl(trimSlot.url) ?? ""} controls className="h-9 w-full" />
            )}
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">
                  {t("characters.voiceSamples.startSeconds")}
                </Label>
                <Input
                  type="number"
                  min="0"
                  step="0.1"
                  value={trimStart}
                  onChange={(event) => setTrimStart(event.target.value)}
                  className="h-10 rounded-[8px] border-white/[0.10] bg-white/[0.04] text-sm shadow-none focus:border-white/[0.18]"
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">
                  {t("characters.voiceSamples.durationSeconds")}
                </Label>
                <Input
                  type="number"
                  min="0.1"
                  max="15"
                  step="0.1"
                  value={trimDuration}
                  onChange={(event) => setTrimDuration(event.target.value)}
                  className="h-10 rounded-[8px] border-white/[0.10] bg-white/[0.04] text-sm shadow-none focus:border-white/[0.18]"
                />
              </div>
            </div>
          </div>
          <DialogFooter className="mt-2 flex flex-col gap-2 sm:flex-row sm:justify-end sm:gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => setTrimSlot(null)}
              className="h-9 rounded-md border-white/[0.10] bg-white/[0.04] px-4 text-sm font-normal text-foreground/80 shadow-none hover:border-white/[0.18] hover:bg-white/[0.07] hover:text-foreground"
            >
              {t("common.cancel")}
            </Button>
            <Button
              type="button"
              onClick={applyTrim}
              disabled={trimVoice.isPending}
              className="h-9 rounded-md bg-primary px-4 text-sm font-normal text-primary-foreground shadow-lg shadow-primary/15 hover:bg-primary/90"
            >
              {trimVoice.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Scissors className="size-4" />
              )}
              {t("characters.voiceSamples.applyTrim")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
