import os
from fastapi import FastAPI, Request, responses
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from app.api.stream_router import router as stream_router
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
    version="1.0.0"
)

# 添加CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有来源，生产环境应限制
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

# 直接运行时的入口点
if __name__ == "__main__":
    # 确保存储目录存在
    os.makedirs("app/storage/generation_states", exist_ok=True)
    os.makedirs("app/storage/partial_contents", exist_ok=True)
    
    # 启动服务器
    uvicorn.run("app.main:app", host=APP_HOST, port=APP_PORT, reload=DEBUG) 