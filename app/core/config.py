import os
from dotenv import load_dotenv
from typing import Optional

# 加载环境变量
load_dotenv()

# 基础目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# API设置
API_KEY = os.getenv("API_KEY", "")
API_URL = os.getenv("API_URL", "https://vip.apiyi.com/v1/chat/completions")
API_VERSION = os.getenv("API_VERSION", "2023-06-01")

# 应用设置
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8003"))
DEBUG = os.getenv("DEBUG", "True").lower() == "true"

# AI模型设置
MODEL_NAME = os.getenv("MODEL_NAME", "claude-3-7-sonnet-20250219")

# 路径设置
STORAGE_DIR = os.path.join(BASE_DIR, "app/storage")
GENERATION_STATES_DIR = os.path.join(STORAGE_DIR, "generation_states")
PARTIAL_CONTENTS_DIR = os.path.join(STORAGE_DIR, "partial_contents")
PDFS_DIR = os.path.join(STORAGE_DIR, "pdfs")  # 新增PDF存储目录
IMAGES_DIR = os.path.join(STORAGE_DIR, "images")  # 新增图片存储目录

# 创建存储目录
os.makedirs(GENERATION_STATES_DIR, exist_ok=True)
os.makedirs(PARTIAL_CONTENTS_DIR, exist_ok=True)
os.makedirs(PDFS_DIR, exist_ok=True)  # 确保PDF目录存在
os.makedirs(IMAGES_DIR, exist_ok=True)  # 确保图片目录存在

# 令牌限制
DIRECTORY_TOKEN_LIMIT = 5000 # 目录生成限制
EPISODE_TOKEN_LIMIT = 30000 # 单集生成限制
RESUME_TOKEN_LIMIT = 20000 # 续写生成限制

# 请求超时(秒)
REQUEST_TIMEOUT = 600

# RunningHub API 设置
# 创建任务API
RUNNINGHUB_CREATE_API_URL = os.getenv("RUNNINGHUB_CREATE_API_URL", "https://www.runninghub.cn/task/openapi/create")
# 查询任务状态API
RUNNINGHUB_STATUS_API_URL = os.getenv("RUNNINGHUB_STATUS_API_URL", "https://www.runninghub.cn/task/openapi/status")
# 查询任务结果API
RUNNINGHUB_RESULT_API_URL = os.getenv("RUNNINGHUB_RESULT_API_URL", "https://www.runninghub.cn/task/openapi/outputs")
# 取消任务API
RUNNINGHUB_CANCEL_API_URL = os.getenv("RUNNINGHUB_CANCEL_API_URL", "https://www.runninghub.cn/task/openapi/cancel")
# API密钥和工作流配置
RUNNINGHUB_API_KEY = os.getenv("RUNNINGHUB_API_KEY", "d9ce3b3ace1242f5824396f69c33f0f3") # 默认值需要替换为实际的API Key
RUNNINGHUB_WORKFLOW_ID = os.getenv("RUNNINGHUB_WORKFLOW_ID", "1917109902920323073")
RUNNINGHUB_NODE_ID = os.getenv("RUNNINGHUB_NODE_ID", "147")