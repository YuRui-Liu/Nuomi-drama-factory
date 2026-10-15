from fastapi import APIRouter, Depends, HTTPException
from novelvideo.api.deps import get_api_user
from novelvideo.freezone.video_processing_newapi import ProcessingConfig, config_path, read_config

router = APIRouter()


@router.get("/projects/{project}/freezone/video/processing-config")
async def get_config(project: str, user: dict = Depends(get_api_user)):
    from novelvideo.api.routes.freezone import _resolve_freezone_project
    _, _, _, root, _ = await _resolve_freezone_project(project, user)
    return {"ok": True, "data": read_config(root).model_dump()}


@router.put("/projects/{project}/freezone/video/processing-config")
async def put_config(project: str, body: ProcessingConfig, user: dict = Depends(get_api_user)):
    from novelvideo.api.routes.freezone import _resolve_freezone_project
    _, _, _, root, _ = await _resolve_freezone_project(project, user)
    for mode, model in body.models.items():
        if any(fps < 1 or fps > 240 for fps in model.frame_rates):
            raise HTTPException(422, "帧率必须在 1–240 范围内")
        if mode != "extend" and model.output_kind != "full":
            raise HTTPException(422, "只有延长支持续段输出")
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    from novelvideo.freezone.video_processing_newapi import _save
    _save(path, body.model_dump())
    return {"ok": True, "data": body.model_dump()}
