import os
from fastapi import FastAPI, Request, responses
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from app.api.stream_router import router as stream_router
from app.api.pdf_router import router as pdf_router
from app.core.config import APP_HOST, APP_PORT, DEBUG

# 创建应用目录
os.makedirs("app/storage/generation_states", exist_ok=True)
os.makedirs("app/storage/partial_contents", exist_ok=True)

# 设置模板和静态文件目录的绝对路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

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

# 挂载静态文件目录
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# 添加主页路由
@app.get("/")
async def get_index(request: Request):
    """返回主页模板"""
    return templates.TemplateResponse("index.html", {"request": request})

# 挂载流式API路由
app.include_router(stream_router, prefix="/api")
app.include_router(pdf_router, prefix="/api/pdf")

# 直接运行时的入口点
if __name__ == "__main__":
    # 确保存储目录存在
    os.makedirs("app/storage/generation_states", exist_ok=True)
    os.makedirs("app/storage/partial_contents", exist_ok=True)
    
    # 启动服务器
    uvicorn.run("app.main:app", host=APP_HOST, port=APP_PORT, reload=DEBUG) 