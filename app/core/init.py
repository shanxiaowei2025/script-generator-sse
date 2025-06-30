import os
from app.core.config import (
    MINIO_ENABLED,
    GENERATION_STATES_DIR,
    PARTIAL_CONTENTS_DIR,
    PDFS_DIR,
    IMAGES_DIR
)

def create_storage_directories():
    """创建应用所需的存储目录"""
    os.makedirs(GENERATION_STATES_DIR, exist_ok=True)
    os.makedirs(PARTIAL_CONTENTS_DIR, exist_ok=True)
    os.makedirs(PDFS_DIR, exist_ok=True)  # PDF存储目录
    os.makedirs(IMAGES_DIR, exist_ok=True)  # 图片存储目录
    print("已创建存储目录")

def initialize_minio():
    """初始化MinIO客户端"""
    if MINIO_ENABLED:
        from app.utils.minio_storage import minio_client
        if minio_client.is_available():
            print("MinIO存储已成功初始化")
        else:
            print("警告: MinIO存储初始化失败，将使用本地存储") 