"""Translate frozen node inputs to existing, typed Freezone service requests."""

from __future__ import annotations

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
