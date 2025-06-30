from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
import asyncio

from app.models.schema import (
    StreamScriptGenerationRequest,
    ExtractScenePromptsRequest,
)
from app.api.models import (
    RunningHubProcessRequest,
    RunningHubTaskStatusRequest,
    RunningHubTaskResultRequest,
    RunningHubTaskCancelRequest
)
from app.services.script_generation import stream_generate_script_service
from app.services.scene_prompts import extract_scene_prompts_service
from app.services.runninghub import (
    process_prompts_service,
    get_task_status_service,
    get_task_result_service,
    cancel_task_service
)
from app.services.pdf_generation import generate_script_pdf_path_service

# 创建路由
router = APIRouter()

# 脚本生成路由
@router.post("/stream/generate-script")
async def stream_generate_script(request: StreamScriptGenerationRequest):
    """流式生成脚本API"""
    return await stream_generate_script_service(request)

# 取消生成任务
@router.delete("/stream/cancel/{task_id}")
async def cancel_streaming(task_id: str):
    """取消流式生成任务"""
    return await cancel_task_service(task_id)

# 提取场景提示词
@router.post("/stream/extract-scene-prompts")
async def stream_extract_scene_prompts(request: ExtractScenePromptsRequest):
    """流式提取场景提示词"""
    return await extract_scene_prompts_service(request)

# 使用RunningHub处理提示词
@router.post("/stream/process-prompts-with-runninghub")
async def process_prompts_with_runninghub(request: RunningHubProcessRequest):
    """使用RunningHub处理提示词"""
    return await process_prompts_service(request)

# 查询RunningHub任务状态
@router.post("/runninghub/task-status")
async def get_runninghub_task_status(request: RunningHubTaskStatusRequest):
    """查询RunningHub任务状态"""
    return await get_task_status_service(request)

# 查询RunningHub任务结果
@router.post("/runninghub/task-result")
async def get_runninghub_task_result(request: RunningHubTaskResultRequest):
    """查询RunningHub任务结果"""
    return await get_task_result_service(request)

# 取消RunningHub任务
@router.post("/runninghub/task-cancel")
async def cancel_runninghub_task_route(request: RunningHubTaskCancelRequest):
    """取消RunningHub任务"""
    return await cancel_task_service(request.request_id)

# 生成脚本PDF路径
@router.get("/generate-script-pdf-path/{task_id}")
async def generate_script_pdf_path(task_id: str, timeout: int = 60):
    """生成脚本PDF并返回路径"""
    return await generate_script_pdf_path_service(task_id, timeout)