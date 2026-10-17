"""Translate frozen node inputs to existing, typed Freezone service requests."""

from __future__ import annotations

import math
from copy import deepcopy

from ..models import OrchestrationError

ROUTES = {
    ("imageGenNode", "generate"): ("freezone_gen", "FreezoneGenRequest"),
    ("imageGenNode", "edit"): ("freezone_edit", "FreezoneEditRequest"),
    ("imageNode", "edit"): ("freezone_edit", "FreezoneEditRequest"),
    ("storyboardGenNode", "generate"): ("freezone_gen", "FreezoneGenRequest"),
    ("scriptNode", "generate"): ("freezone_story_script_generate", "FreezoneStoryScriptGenerateRequest"),
    ("videoNode", "generate"): ("freezone_video_gen", "FreezoneVideoGenRequest"),
    ("videoNode", "image_to_video"): ("freezone_video_i2v", "FreezoneImageToVideoRequest"),
    ("videoNode", "reference"): ("freezone_video_omni_gen", "FreezoneVideoOmniGenRequest"),
    ("videoNode", "keyframes"): ("freezone_video_keyframes", "FreezoneKeyframeVideoRequest"),
    ("videoNode", "edit"): ("freezone_video_edit", "FreezoneVideoEditRequest"),
    ("videoNode", "upscale"): ("freezone_video_upscale", "FreezoneVideoUpscaleRequest"),
    ("videoNode", "erase"): ("freezone_video_erase", "FreezoneVideoEraseRequest"),
    ("videoNode", "extract_frames"): ("freezone_extract_frames", "FreezoneExtractFramesRequest"),
    ("videoNode", "separate_audio"): ("freezone_audio_separate", "FreezoneAudioSeparateRequest"),
    ("videoStoryNode", "analyze"): ("freezone_analyze_video_story", "FreezoneAnalyzeVideoStoryRequest"),
    ("audioNode", "speech"): ("freezone_audio_speech", "FreezoneAudioSpeechRequest"),
    ("audioNode", "music"): ("freezone_audio_eleven_music", "FreezoneAudioMusicRequest"),
    ("videoComposeNode", "compose"): ("freezone_video_compose", "FreezoneVideoComposeRequest"),
    ("threeDWorldNode", "generate"): ("freezone_image_to_3gs", "FreezoneImageTo3GSRequest"),
}


def ordered_upstream(data: dict, upstream: list[dict]) -> list[dict]:
    order = data.get("referenceOrder", [])
    rank = {key: index for index, key in enumerate(order)}
    return sorted(upstream, key=lambda a: (rank.get(a["node_id"], len(rank)), a["node_id"]))


def build_request(node: dict, operation: str, upstream: list[dict], *, canvas_id: str, attempt_id: str) -> tuple[str, dict]:
    data = node.get("data", {})
    kind = node["type"]
    route = ROUTES.get((kind, operation))
    if route is None:
        raise OrchestrationError("operation_unavailable", "No adapter for this operation")
    inputs = ordered_upstream(data, upstream)
    results = [a.get("result") or {} for a in inputs]
    images = [r["imageUrl"] for r in results if r.get("imageUrl")]
    videos = [r.get("videoUrl") or r.get("resultVideoUrl") for r in results if r.get("videoUrl") or r.get("resultVideoUrl")]
    texts = [r["text"] for r in results if r.get("text")]
    for r in results:
        if r.get("scriptResult"):
            import json
            texts.append(json.dumps(r["scriptResult"], ensure_ascii=False))
    prompt = "\n\n".join([str(data.get("prompt") or "")] + texts).strip()
    body = {"prompt": prompt, "canvas_id": canvas_id, "node_id": node["id"]}
    for source, target in (("aspectRatio", "aspect_ratio"), ("model", "model"), ("imageSize", "image_size"),
                           ("size", "image_size"), ("quality", "quality"), ("durationSec", "duration_seconds"),
                           ("generateAudio", "generate_audio"), ("resolution", "resolution")):
        if data.get(source) is not None:
            body[target] = data[source]
    if kind in {"imageNode", "imageGenNode", "storyboardGenNode"}:
        if int(data.get("count") or 1) != 1:
            raise OrchestrationError("unsupported_parameter", "Orchestration image count currently requires 1")
        model = str(body.get("model") or "")
        if "/" in model and model.split("/", 1)[0] in {"openai", "openrouter", "huimeng"}:
            body["provider"], body["model"] = model.split("/", 1)
        if data.get("referenceImageUrl"):
            images.insert(0, data["referenceImageUrl"])
        if operation == "edit":
            if not images and not data.get("request", {}).get("base_url"):
                raise OrchestrationError("input_required", "Image editing requires a source image")
            if images:
                body.update(base_url=images[0], extra_reference_urls=images[1:])
        else:
            body["reference_urls"] = images
    elif kind == "scriptNode":
        body["source_text"] = data.get("sourceText") or data.get("text") or "\n".join(texts)
    elif kind == "audioNode":
        body["text" if operation == "speech" else "input"] = data.get("text") or prompt
        for field in ("voice_ref", "emotion_prompt", "music_length_ms", "force_instrumental"):
            if field in data:
                body[field] = data[field]
    elif kind == "videoComposeNode":
        body = _compose(data, inputs, canvas_id)
    elif kind == "videoNode":
        if operation == "image_to_video":
            body["image_urls"] = images
        elif operation == "keyframes":
            body.update(first_frame_url=images[0] if images else None, last_frame_url=images[1] if len(images) > 1 else None)
        elif operation == "reference":
            body["references"] = [{"type": "image", "url": url} for url in images]
        if videos:
            body["video_url"] = videos[0]
    elif kind == "videoStoryNode" and videos:
        body["video_url"] = videos[0]
    elif kind == "threeDWorldNode" and images:
        body.update(source_url=images[0], source_kind=data.get("plyKind", "master"))
    if not (kind == "videoComposeNode" and "draftTimeline" in data):
        body.update(deepcopy(data.get("request", {})))
    if kind == "storyboardGenNode":
        if data.get('storyboardMode') == 'narrative' or data.get('group_id'):
            from .storyboard import compile_canvas_storyboard
            compiled = compile_canvas_storyboard(data, inputs)
            body['prompt'] = compiled['prompt']
            body['reference_urls'] = [r['url'] for r in compiled['references']]
        else:
            rows = data.get("gridRows", data.get("rows", 2))
            cols = data.get("gridCols", data.get("cols", 2))
            notes = "\n".join(f"分镜{i + 1}：{frame['description']}" for i, frame in
                              enumerate(data.get("frames", [])) if frame.get("description", "").strip())
            body["prompt"] = f"生成 {rows} 行 {cols} 列分镜图，每格等大、无边框、无间隙、无文字、无编号、无字幕；按从左到右从上到下叙事，保持角色一致。\n{prompt}\n{notes}".strip()
    if kind in {"imageNode", "imageGenNode", "storyboardGenNode"}:
        # Visible node controls are authoritative over legacy request overrides.
        for source, target in (("model", "model"), ("size", "image_size"), ("quality", "quality")):
            if data.get(source) is not None:
                body[target] = data[source]
        model = str(body.get("model") or "")
        if "/" in model and model.split("/", 1)[0] in {"openai", "openrouter", "huimeng", "grsai", "newapi"}:
            body["provider"], body["model"] = model.split("/", 1)
        ratio = data.get("requestAspectRatio")
        if not ratio or ratio == "auto":
            ratio = data.get("aspectRatio")
        if ratio and ratio != "auto":
            body["aspect_ratio"] = ratio
        if "styleTemplateId" in data:
            body["style"] = {"template_id": data["styleTemplateId"]} if data["styleTemplateId"] else None
        if "cameraSelection" in data:
            camera = data.get("cameraSelection") or {}
            body["camera"] = {target: camera[source] for source, target in (
                ("cameraBodyId", "camera_body"), ("lensId", "lens"),
                ("focalLengthMm", "focal_length_mm"), ("aperture", "aperture")) if camera.get(source) is not None} or None
        if data.get("extensionStyleId"):
            from novelvideo.services.style_service import StyleService
            from novelvideo.extension_styles import FRAGMENT_KEYS
            style = StyleService.get_extension_style(data["extensionStyleId"])
            if style is None:
                raise OrchestrationError("unknown_style", "Unknown extension style", style_id=data["extensionStyleId"])
            fragment = ", ".join(phrase.strip() for key in FRAGMENT_KEYS for phrase in style.prompt_fragment[key] if phrase.strip())
            body["prompt"] += f"\n\n[Extension style: {style.name}]\n{fragment}"
    # Attribution is owned by the scheduler, never a user-supplied request body.
    body.update(canvas_id=canvas_id, node_id=node["id"])
    return route[0], body


def director_draft(draft: dict) -> dict:
    """Accept the browser's persisted camelCase contract and the API wire form."""
    names = {"schemaVersion": "schema_version", "modelId": "model_id", "aspectRatio": "aspect_ratio",
             "durationSeconds": "duration_seconds", "firstFrame": "first_frame", "lastFrame": "last_frame",
             "imageId": "image_id", "assetId": "asset_id", "characterId": "character_id", "variantId": "variant_id",
             "variantLabel": "variant_label", "assetKind": "asset_kind",
             "caseReference": "case_reference", "proposalId": "proposal_id", "caseId": "case_id",
             "sourcePrompts": "source_prompts", "appliedAt": "applied_at", "promptSha256": "prompt_sha256",
             "upstreamId": "upstream_id", "sourceUrl": "source_url", "contentSha256": "content_sha256"}
    def convert(value):
        if isinstance(value, dict):
            return {names.get(key, key): convert(item) for key, item in value.items()}
        if isinstance(value, list):
            return [convert(item) for item in value]
        return value
    return convert(draft)


def project_director_inputs(node: dict, upstream: list[dict], edges: list[dict]) -> dict:
    """Preserve explicit frame slots alongside optional identity references."""
    data = node.get('data', {})
    draft = director_draft(deepcopy(data.get('draft') or data.get('request', {}).get('draft') or {}))
    mode = data.get('activeInputMode', 'ref')
    by_id = {s['id']: s for s in draft.get('segments', [])}
    incoming = {e['source']: e for e in edges if e['target'] == node['id']}
    refs = list(draft.get('references', [])) if mode == 'ref' else []
    for item in ordered_upstream(data, upstream):
        url = (item.get('result') or {}).get('imageUrl')
        if not url:
            continue
        image = {'image_id': item['node_id'], 'url': url}
        slot = incoming.get(item['node_id'], {}).get('data', {}).get('slot', {})
        if mode == 'ref' and slot.get('kind', 'reference') == 'reference':
            refs.append(image)
        elif slot.get('kind') in {'firstFrame', 'lastFrame', 'first', 'last'}:
            segment = by_id.get(slot.get('segmentId'))
            if segment is not None:
                segment['first_frame' if slot['kind'] in {'firstFrame', 'first'} else 'last_frame'] = image
    draft['references'] = list({r['image_id']: r for r in refs}.values())
    return draft


def _compose(data, inputs, canvas_id):
    if "draftTimeline" in data:
        return _compose_draft_timeline(data.get("draftTimeline"), canvas_id)
    request = deepcopy(data.get("request", {}))
    if request.get("tracks"):
        return request
    timeline = data.get("timeline")
    if not timeline:
        raise OrchestrationError("timeline_required", "Composition requires an explicit timeline")
    by_id = {a["node_id"]: a.get("result") or {} for a in inputs}
    items, cursor = [], 0.0
    for entry in timeline:
        source = by_id.get(entry["node_id"], {})
        url = source.get("videoUrl") or source.get("resultVideoUrl")
        duration = float(entry["duration"])
        if not url or duration <= 0:
            raise OrchestrationError("input_required", "Timeline source video or duration is missing")
        items.append({"item_id": entry["node_id"], "source_url": url, "timeline_start": cursor,
                      "source_start": float(entry.get("source_start", 0)),
                      "source_end": float(entry.get("source_start", 0)) + duration})
        cursor += duration
    return {"canvas_id": canvas_id, "resolution": data.get("resolution", "1080p"),
            "keep_original_audio": data.get("keepOriginalAudio", True),
            "tracks": [{"track_id": "video", "kind": "video", "items": items}]}


def _compose_draft_timeline(draft, canvas_id):
    """Translate the canvas editor's saved camelCase draft into the Freezone API contract.

    The draft's source URLs are frozen editor choices. Current upstream node results are not
    allowed to silently replace them; source replacement requires an explicit editor action.
    """
    if not isinstance(draft, dict):
        raise OrchestrationError("invalid_timeline", "Saved composition draft must be an object")
    raw_tracks = draft.get("tracks")
    if not isinstance(raw_tracks, list):
        raise OrchestrationError("invalid_timeline", "Saved composition draft has no track list")

    def number(value, label, *, minimum=None, maximum=None, default=None):
        if value is None and default is not None:
            value = default
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise OrchestrationError("invalid_timeline", f"Composition {label} must be numeric")
        parsed = float(value)
        if not math.isfinite(parsed):
            raise OrchestrationError("invalid_timeline", f"Composition {label} must be finite")
        if minimum is not None and parsed < minimum:
            raise OrchestrationError("invalid_timeline", f"Composition {label} is below its supported range")
        if maximum is not None and parsed > maximum:
            raise OrchestrationError("invalid_timeline", f"Composition {label} is above its supported range")
        return parsed

    tracks = []
    track_ids = set()
    item_ids = set()
    for track in raw_tracks:
        if not isinstance(track, dict):
            raise OrchestrationError("invalid_timeline", "Saved composition draft contains an invalid track")
        track_id = str(track.get("id") or "")
        kind = track.get("kind")
        clips = track.get("clips")
        if not track_id or track_id in track_ids or kind not in {"video", "audio"} or not isinstance(clips, list):
            raise OrchestrationError("invalid_timeline", "Saved composition draft contains an invalid track")
        track_ids.add(track_id)
        items = []
        for clip in clips:
            if not isinstance(clip, dict):
                raise OrchestrationError("invalid_timeline", "Saved composition draft contains an invalid clip")
            item_id = str(clip.get("id") or "")
            source_url = str(clip.get("sourceUrl") or "").strip()
            if not item_id or item_id in item_ids or not source_url:
                raise OrchestrationError("invalid_timeline", "Saved composition clip needs unique identity and source URL")
            item_ids.add(item_id)
            start_ms = number(clip.get("timelineStartMs"), "timeline position", minimum=0)
            source_start_ms = number(clip.get("trimStartMs"), "source trim start", minimum=0)
            source_end_ms = number(clip.get("trimEndMs"), "source trim end", minimum=0)
            if source_end_ms <= source_start_ms:
                raise OrchestrationError("invalid_timeline", "Saved composition clip has an invalid source range")
            speed = number(clip.get("speed"), "speed", minimum=0.25, maximum=4, default=1)
            volume = number(clip.get("volume"), "volume", minimum=0, maximum=2, default=1)
            dubbing = clip.get("dubbingSource")
            dubbing_source = None
            if dubbing is not None:
                if not isinstance(dubbing, dict):
                    raise OrchestrationError("invalid_timeline", "Saved composition clip has invalid dubbing provenance")
                take_id = str(dubbing.get("takeId") or "").strip()
                dialogue_id = str(dubbing.get("dialogueId") or "").strip()
                group_id = str(dubbing.get("groupId") or "").strip()
                if not take_id or not dialogue_id or not group_id:
                    raise OrchestrationError("invalid_timeline", "Saved composition dubbing provenance is incomplete")
                dubbing_source = {
                    "take_id": take_id,
                    "dialogue_id": dialogue_id,
                    "group_id": group_id,
                }
                if dubbing.get("episode") is not None:
                    episode = dubbing["episode"]
                    if isinstance(episode, bool) or not isinstance(episode, int) or episode < 1:
                        raise OrchestrationError("invalid_timeline", "Saved composition dubbing episode is invalid")
                    dubbing_source["episode"] = episode
            muted = clip.get("muted", False)
            if not isinstance(muted, bool):
                raise OrchestrationError("invalid_timeline", "Saved composition clip mute state is invalid")
            items.append({
                "item_id": item_id,
                "source_url": source_url,
                "source_node_id": str(clip.get("nodeId") or "") or None,
                "dubbing_source": dubbing_source,
                "timeline_start": max(0.0, start_ms) / 1000,
                "source_start": source_start_ms / 1000,
                "source_end": source_end_ms / 1000,
                "volume": volume,
                "muted": muted,
                "speed": speed,
            })
        if items:
            tracks.append({"track_id": track_id, "kind": kind, "items": items})
    if not any(track["kind"] == "video" and track["items"] for track in tracks):
        raise OrchestrationError("invalid_timeline", "Saved composition draft needs at least one video clip")

    raw_subtitle_track = draft.get("subtitleTrack")
    subtitle_tracks = []
    if raw_subtitle_track is not None:
        if not isinstance(raw_subtitle_track, dict) or not isinstance(raw_subtitle_track.get("cues", []), list):
            raise OrchestrationError("invalid_timeline", "Saved composition draft has an invalid subtitle track")
        subtitle_track_id = str(raw_subtitle_track.get("id") or "")
        if not subtitle_track_id:
            raise OrchestrationError("invalid_timeline", "Saved subtitle track needs an identity")
        cues = []
        cue_ids = set()
        for cue in raw_subtitle_track.get("cues", []):
            if not isinstance(cue, dict):
                raise OrchestrationError("invalid_timeline", "Saved subtitle track contains an invalid cue")
            cue_id = str(cue.get("id") or "")
            text = str(cue.get("text") or "")
            if not cue_id or cue_id in cue_ids:
                raise OrchestrationError("invalid_timeline", "Subtitle cues need unique identities")
            cue_ids.add(cue_id)
            start = number(cue.get("timelineStartMs"), "subtitle start", minimum=0)
            end = number(cue.get("timelineEndMs"), "subtitle end", minimum=0)
            if end <= start:
                raise OrchestrationError("invalid_timeline", "Saved subtitle cue has an invalid time range")
            timing = cue.get("timing", "manual")
            if timing not in {"manual", "estimated_audio_clip"}:
                raise OrchestrationError("invalid_timeline", "Saved subtitle cue has an invalid timing mode")
            source_timing = cue.get("sourceTiming")
            wire_source_timing = None
            if source_timing is not None:
                if not isinstance(source_timing, dict):
                    raise OrchestrationError("invalid_timeline", "Saved subtitle source timing is invalid")
                wire_source_timing = {
                    "timeline_start": number(source_timing.get("timelineStartMs"), "source timing start", minimum=0) / 1000,
                    "timeline_end": number(source_timing.get("timelineEndMs"), "source timing end", minimum=0) / 1000,
                    "source_start": number(source_timing.get("sourceStartMs"), "source trim start", minimum=0) / 1000,
                    "source_end": number(source_timing.get("sourceEndMs"), "source trim end", minimum=0) / 1000,
                    "speed": number(source_timing.get("speed"), "source speed", minimum=0.25, maximum=4),
                }
                if wire_source_timing["source_end"] <= wire_source_timing["source_start"]:
                    raise OrchestrationError("invalid_timeline", "Saved subtitle source trim range is invalid")
            confirmed = cue.get("timingConfirmed", False)
            if not isinstance(confirmed, bool):
                raise OrchestrationError("invalid_timeline", "Saved subtitle timing confirmation is invalid")
            cues.append({
                "cue_id": cue_id,
                "text": text,
                "timeline_start": start / 1000,
                "timeline_end": end / 1000,
                "source_clip_id": str(cue.get("sourceClipId") or "") or None,
                "source_clip_url": str(cue.get("sourceClipUrl") or "") or None,
                "source_take_id": str(cue.get("sourceTakeId") or "") or None,
                "source_dialogue_id": str(cue.get("sourceDialogueId") or "") or None,
                "source_group_id": str(cue.get("sourceGroupId") or "") or None,
                "source_timing": wire_source_timing,
                "timing": timing,
                "timing_confirmed": confirmed,
            })
        if cues:
            subtitle_tracks.append({"track_id": subtitle_track_id, "cues": cues})

    resolution = draft.get("resolution", "1080p")
    if resolution not in {"720p", "1080p"}:
        raise OrchestrationError("invalid_timeline", "Saved composition draft has an unsupported resolution")
    fps = number(draft.get("fps", 30), "FPS", minimum=1, maximum=60)
    if not fps.is_integer():
        raise OrchestrationError("invalid_timeline", "Saved composition draft FPS must be an integer")
    revision = draft.get("draftRevision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise OrchestrationError("invalid_timeline", "Saved composition draft revision is invalid")
    return {
        "canvas_id": canvas_id,
        "resolution": resolution,
        "resolution_origin": draft.get("resolutionOrigin", "default"),
        "fps": int(fps),
        "fps_origin": draft.get("fpsOrigin", "default"),
        "draft_revision": revision,
        "cover_url": (draft.get("cover") or {}).get("url") if isinstance(draft.get("cover"), dict) else "",
        "keep_original_audio": True,
        "tracks": tracks,
        "subtitle_tracks": subtitle_tracks,
    }
