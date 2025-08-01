import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.stream_router import router as stream_router
from app.core.config import APP_HOST, APP_PORT, DEBUG, MINIO_ENABLED
from app.core.init import create_storage_directories, initialize_minio

# 创建存储目录
create_storage_directories()

# 创建FastAPI应用
app = FastAPI(
    title="剧本生成器 API",
    description="基于HTTP流式响应(SSE)的剧本生成服务",
    version="1.0.0",
    openapi_extra={"x-server-timeout": 300}  # 5分钟超时
)

# 添加CORS中间件
app.add_middleware(
    CORSMiddleware,
    # 允许的源列表，也可以使用["*"]允许所有源
    allow_origins=[
        "http://localhost:3000",  # 前端开发服务器
        "http://localhost:8000",  # 可能的其他前端
        "http://localhost:8003",  # 你当前使用的端口
        "http://127.0.0.1:8003",
        "https://yourdomain.com",  # 生产环境域名
        "*"  # 允许所有源（开发环境可用，生产环境谨慎使用）
    ],
    # 允许的请求方法
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    # 允许的请求头
    allow_headers=["Content-Type", "Authorization", "X-Requested-With", "Accept", "Origin", "Access-Control-Request-Method", "Access-Control-Request-Headers"],
    # 允许携带凭证(cookies等)
    allow_credentials=True,
    # 允许暴露的响应头
    expose_headers=["Content-Disposition", "X-Suggested-Filename"],
    # CORS预检请求的缓存时间（秒）
    max_age=600,
)

# 如果启用了MinIO，则不需要挂载本地静态文件服务
if not MINIO_ENABLED:
    # 挂载静态文件服务
    # 提供PDF文件下载
    app.mount("/storage/pdfs", StaticFiles(directory="app/storage/pdfs"), name="pdfs")
    # 提供图片文件访问
    app.mount("/storage/images", StaticFiles(directory="app/storage/images"), name="images")

# 挂载流式API路由
app.include_router(stream_router, prefix="/api")

# 直接运行时的入口点
if __name__ == "__main__":
    # 确保存储目录存在
    create_storage_directories()
    
    # 初始化MinIO客户端
    initialize_minio()
    
    # 启动服务器
    import uvicorn
    uvicorn.run("app.main:app", host=APP_HOST, port=APP_PORT, reload=DEBUG) 