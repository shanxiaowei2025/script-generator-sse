import os
from dotenv import load_dotenv
from typing import Optional

# 加载环境变量
load_dotenv()

# 基础目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# API设置
API_KEY = os.getenv("API_KEY", "")
API_URL = os.getenv("API_URL", "")
API_VERSION = os.getenv("API_VERSION", "2023-06-01")

# 应用设置
APP_HOST = os.getenv("APP_HOST", "")
APP_PORT = int(os.getenv("APP_PORT", ""))
DEBUG = os.getenv("DEBUG", "True").lower() == "true"

# AI模型设置
MODEL_NAME = os.getenv("MODEL_NAME", "")

# 路径设置
STORAGE_DIR = os.path.join(BASE_DIR, "app/storage")
GENERATION_STATES_DIR = os.path.join(STORAGE_DIR, "generation_states")
PARTIAL_CONTENTS_DIR = os.path.join(STORAGE_DIR, "partial_contents")
PDFS_DIR = os.path.join(STORAGE_DIR, "pdfs")  # 新增PDF存储目录
IMAGES_DIR = os.path.join(STORAGE_DIR, "images")  # 新增图片存储目录

# MinIO配置
MINIO_ENABLED = os.getenv("MINIO_ENABLED", "true") # 默认启用MinIO
MINIO_URL = os.getenv("MINIO_URL", "")  # MinIO服务URL
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "")  # MinIO访问密钥
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")  # MinIO访问密钥
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "script-generator")  # 存储桶名称
MINIO_SECURE = os.getenv("MINIO_SECURE", "true").lower() == "true"  # 是否使用SSL/TLS连接
MINIO_REGION = os.getenv("MINIO_REGION", "")  # 区域名称，可选
# 是否将文件保存在本地（默认不保存本地，直接上传到MinIO）
SAVE_FILES_LOCALLY = os.getenv("SAVE_FILES_LOCALLY", "false").lower() == "true"

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
RUNNINGHUB_CREATE_API_URL = os.getenv("RUNNINGHUB_CREATE_API_URL", "")
# 查询任务状态API
RUNNINGHUB_STATUS_API_URL = os.getenv("RUNNINGHUB_STATUS_API_URL", "")
# 查询任务结果API
RUNNINGHUB_RESULT_API_URL = os.getenv("RUNNINGHUB_RESULT_API_URL", "")
# 取消任务API
RUNNINGHUB_CANCEL_API_URL = os.getenv("RUNNINGHUB_CANCEL_API_URL", "")
# API密钥和工作流配置
RUNNINGHUB_API_KEY = os.getenv("RUNNINGHUB_API_KEY", "") # 默认值需要替换为实际的API Key
RUNNINGHUB_WORKFLOW_ID = os.getenv("RUNNINGHUB_WORKFLOW_ID", "")
RUNNINGHUB_NODE_ID = os.getenv("RUNNINGHUB_NODE_ID", "")