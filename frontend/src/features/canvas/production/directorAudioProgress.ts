type RecordValue = Record<string, unknown>;

export interface DirectorAudioOutputSet {
  audio: string[];
  ambience: string[];
  missing: string[];
  ready: boolean;
}

function record(value: unknown): RecordValue {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as RecordValue : {};
}

export function projectFileFromMediaUrl(project: string, value: unknown): string | null {
  if (typeof value !== 'string' || !value.trim()) return null;
  try {
    const url = new URL(value, typeof window === 'undefined' ? 'http://localhost' : window.location.origin);
    const prefix = `/api/v1/projects/${encodeURIComponent(project)}/media/`;
    if (!url.pathname.startsWith(prefix)) return null;
    const relative = decodeURIComponent(url.pathname.slice(prefix.length));
    const parts = relative.split('/');
    if (!relative || parts.some(part => !part || part === '.' || part === '..' || part.includes('\\'))) return null;
    return parts.join('/');
  } catch {
    return null;
  }
}

export function collectDirectorAudioOutputs(
  project: string,
  groupIds: readonly string[],
  revisionId: string,
  mediaGroups: readonly RecordValue[],
): DirectorAudioOutputSet {
  const audio = new Set<string>();
  const ambience = new Set<string>();
  const missing: string[] = [];

  for (const groupId of groupIds) {
    const group = mediaGroups.find(item => item.id === groupId && item.director_revision_id === revisionId);
    const video = record(record(group?.stages).video);
    if (video.status !== 'completed' || video.needs_regeneration === true) {
      missing.push(`${groupId}：当前版本视频尚未就绪`);
      continue;
    }
    const spans = Array.isArray(video.video_spans) ? video.video_spans.map(record) : null;
    if (!spans) {
      missing.push(`${groupId}：缺少对白来源状态`);
      continue;
    }

    if (spans.some(span => span.has_dialogue !== true && span.has_dialogue !== false)) {
      missing.push(`${groupId}：对白状态缺失`);
      continue;
    }

    const spoken = spans.filter(span => span.has_dialogue === true);
    const hasExternalDialogue = spoken.some(span => span.dialogue_source === 'external_tts');
    const hasNativeDialogue = spoken.some(span => span.dialogue_source === 'h3_native');
    const unknownSource = spoken.some(span => span.dialogue_source !== 'external_tts' && span.dialogue_source !== 'h3_native');
    if (unknownSource) missing.push(`${groupId}：对白来源未选择`);
    if (hasExternalDialogue && hasNativeDialogue) {
      // The native path is a single original mix for the whole group. Mixing it
      // with generated spans would either restore old dialogue or drop native
      // dialogue, so require one explicit source policy per spoken group.
      missing.push(`${groupId}：同组对白来源需统一为独立配音或 H3 原声`);
      continue;
    }

    for (const span of spoken.filter(item => item.dialogue_source === 'external_tts')) {
      const path = projectFileFromMediaUrl(project, span.external_audio_path);
      if (span.external_audio_ready !== true || !path) {
        missing.push(`${groupId}：对白外部配音文件`);
        continue;
      }
      audio.add(path);
    }

    if (hasNativeDialogue) {
      const path = projectFileFromMediaUrl(project, video.original_audio_path);
      if (!path) missing.push(`${groupId}：原生对白的原始音频文件`);
      else audio.add(path);
    }

    const ambiencePath = projectFileFromMediaUrl(project, video.ambience_stem_path);
    if (hasExternalDialogue) {
      if (video.ambience_stem_status !== 'succeeded' || !ambiencePath) {
        missing.push(`${groupId}：分离环境音文件`);
      } else ambience.add(ambiencePath);
    } else if (spoken.length === 0 && video.ambience_stem_status === 'succeeded' && ambiencePath) {
      // Dialogue-free spans need no dub. A real generated ambience stem can still be
      // recorded as the group's audio artifact when one is available.
      audio.add(ambiencePath);
      ambience.add(ambiencePath);
    }
  }

  return { audio: [...audio], ambience: [...ambience], missing, ready: missing.length === 0 };
}
