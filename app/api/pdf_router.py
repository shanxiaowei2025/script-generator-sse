from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import os

from app.utils.pdf_generator import generate_script_pdf, get_images_data
from app.utils.storage import load_generation_state
from app.api.stream_router import global_tasks_status  # 导入全局任务状态

router = APIRouter()

class PDFGenerationRequest(BaseModel):
    script_task_id: str
    images_request_id: str
    title: Optional[str] = "AI生成剧本与图片"

@router.post("/generate-pdf")
async def create_pdf(request: PDFGenerationRequest):
    """生成包含剧本和图片的PDF文件"""
    try:
        # 获取剧本数据
        script_data = load_generation_state(request.script_task_id)
        if not script_data:
            raise HTTPException(status_code=404, detail="剧本未找到")
        
        # 获取图片数据
        images_data = await get_images_data(request.images_request_id)
        
        # 生成PDF
        pdf_result = await generate_script_pdf(script_data, images_data, title=request.title)
        
        return {
            "status": "success",
            "message": "PDF生成成功",
            "download_url": pdf_result["download_url"]
        }
    except Exception as e:
        import traceback
        error_detail = str(e) + "\n" + traceback.format_exc()
        raise HTTPException(status_code=500, detail=f"PDF生成失败: {error_detail}")

@router.get("/download-pdf/{filename}")
async def download_pdf(filename: str):
    """下载生成的PDF文件"""
    file_path = f"app/static/pdf/{filename}"
    try:
        return FileResponse(
            path=file_path,
            filename=filename,
            media_type='application/pdf'
        )
    except Exception:
        raise HTTPException(status_code=404, detail="PDF文件未找到")

@router.get("/debug-task-data/{request_id}")
async def debug_task_data(request_id: str):
    """调试任务数据格式"""
    try:
        # 查找相关任务
        related_tasks = {}
        for subtask_id, task_info in global_tasks_status.items():
            if task_info.get("request_id") == request_id:
                # 保存基本信息和结果结构
                result = task_info.get("result", {})
                if isinstance(result, dict) and "final_result" in result:
                    # 只保存结构而不是全部内容
                    related_tasks[subtask_id] = {
                        "has_result": True,
                        "result_structure": get_dict_structure(result),
                        "prompt": result.get("prompt")
                    }
                else:
                    related_tasks[subtask_id] = {
                        "has_result": False
                    }
        
        return {
            "request_id": request_id,
            "related_tasks_count": len(related_tasks),
            "sample_tasks": list(related_tasks.keys())[:5],
            "sample_data": list(related_tasks.values())[:5]
        }
    except Exception as e:
        return {"error": str(e)}

def get_dict_structure(d, max_depth=3, current_depth=0):
    """递归获取字典结构，不包含实际值"""
    if current_depth >= max_depth:
        return "..."
    
    if isinstance(d, dict):
        return {k: get_dict_structure(v, max_depth, current_depth+1) for k, v in d.items()}
    elif isinstance(d, list):
        if d:
            return [get_dict_structure(d[0], max_depth, current_depth+1), "..."]
        else:
            return []
    else:
        return type(d).__name__
