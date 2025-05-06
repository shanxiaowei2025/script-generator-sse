from pydantic import BaseModel, Field
from typing import List, Optional, Dict


# 流式API请求模型
class StreamScriptGenerationRequest(BaseModel):
    """剧本生成请求模型 - 用于流式API"""
    genre: str = Field(..., description="剧本题材")
    duration: str = Field(..., description="每集时长")
    episodes: int = Field(..., description="剧本集数")
    characters: List[str] = Field(..., description="角色列表")
    api_key: Optional[str] = Field(None, description="自定义API密钥")
    api_url: Optional[str] = Field(None, description="自定义API URL")


class EpisodeGenerationRequest(StreamScriptGenerationRequest):
    """单集生成请求"""
    episode: int = Field(..., description="要生成的集数")


class GenerationStatusRequest(BaseModel):
    """生成状态请求"""
    client_id: str = Field(..., description="客户端ID")


# 响应模型
class InitialContentResponse(BaseModel):
    """初始内容（角色表和目录）响应"""
    content: str = Field(..., description="角色表和目录内容")


class EpisodeContentResponse(BaseModel):
    """单集内容响应"""
    episode: int = Field(..., description="集数")
    content: str = Field(..., description="单集内容")


class FullScriptResponse(BaseModel):
    """完整剧本响应"""
    full_script: str = Field(..., description="完整剧本内容")
    episodes_completed: int = Field(..., description="已完成集数")
    total_episodes: int = Field(..., description="总集数")


class GenerationStatusResponse(BaseModel):
    """生成状态响应"""
    client_id: str = Field(..., description="客户端ID")
    episodes_completed: int = Field(..., description="已完成集数")
    status: str = Field(..., description="生成状态")


class ErrorResponse(BaseModel):
    """错误响应"""
    detail: str = Field(..., description="错误详情")


class ExtractScenePromptsRequest(BaseModel):
    """提取画面描述词请求 - 用于流式API"""
    task_id: str = Field(..., description="任务ID")
    episode: Optional[int] = Field(None, description="指定要提取的集数，不指定则返回所有集")


class ScenePromptsResponse(BaseModel):
    """画面描述词响应"""
    content: Dict[str, str] = Field(..., description="按集数组织的画面描述词，key为集数，value为该集的画面描述词") 