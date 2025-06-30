from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List


class RunningHubProcessRequest(BaseModel):
    """RunningHub处理请求模型"""
    task_id: str = Field(..., description="任务ID")
    episode: Optional[int] = Field(None, description="指定要处理的集数，不指定则处理所有集")
    auto_download: Optional[bool] = Field(True, description="是否自动下载图片")


class RunningHubTaskStatusRequest(BaseModel):
    """RunningHub任务状态请求模型"""
    task_id: str = Field(..., description="RunningHub任务ID")


class RunningHubTaskResultRequest(BaseModel):
    """RunningHub任务结果请求模型"""
    task_id: str = Field(..., description="RunningHub任务ID")


class RunningHubTaskCancelRequest(BaseModel):
    """RunningHub任务取消请求模型"""
    request_id: str = Field(..., description="请求ID") 