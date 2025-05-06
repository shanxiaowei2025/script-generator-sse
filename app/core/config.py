import os
from dotenv import load_dotenv
from typing import Optional

# 加载环境变量
load_dotenv()

# API设置
API_KEY = os.getenv("API_KEY", "")
API_URL = os.getenv("API_URL", "https://api.anthropic.com/v1/messages")
API_VERSION = os.getenv("API_VERSION", "2023-06-01")

# 应用设置
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8003"))
DEBUG = os.getenv("DEBUG", "True").lower() == "true"

# AI模型设置
MODEL_NAME = os.getenv("MODEL_NAME", "claude-3-7-sonnet-20250219")

# 路径设置
STORAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "app/storage")
GENERATION_STATES_DIR = os.path.join(STORAGE_DIR, "generation_states")
PARTIAL_CONTENTS_DIR = os.path.join(STORAGE_DIR, "partial_contents")

# 创建存储目录
os.makedirs(GENERATION_STATES_DIR, exist_ok=True)
os.makedirs(PARTIAL_CONTENTS_DIR, exist_ok=True)

# 令牌限制
DIRECTORY_TOKEN_LIMIT = 5000 # 目录生成限制
EPISODE_TOKEN_LIMIT = 20000 # 单集生成限制
RESUME_TOKEN_LIMIT = 20000 # 续写生成限制

# 请求超时(秒)
REQUEST_TIMEOUT = 600 