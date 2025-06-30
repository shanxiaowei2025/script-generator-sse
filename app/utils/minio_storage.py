import os
import io
import tempfile
from typing import Optional, BinaryIO, List, Dict, Any, Tuple
from minio import Minio
from minio.error import S3Error
from urllib.parse import urlparse
import aiohttp
import asyncio
import aiofiles
from datetime import datetime, timedelta

from app.core.config import (
    MINIO_URL, 
    MINIO_ACCESS_KEY, 
    MINIO_SECRET_KEY,
    MINIO_BUCKET,
    MINIO_SECURE,
    MINIO_REGION,
    MINIO_ENABLED
)

# 常量定义
PDF_PREFIX = "pdfs/"
IMAGE_PREFIX = "images/"
STATE_PREFIX = "states/"
CONTENT_PREFIX = "contents/"

class MinioClient:
    """MinIO客户端封装类，提供文件上传下载功能"""
    
    _instance = None
    
    def __new__(cls):
        """单例模式实现"""
        if cls._instance is None:
            cls._instance = super(MinioClient, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """初始化MinIO客户端"""
        if self._initialized:
            return
            
        self._client = None
        self._bucket = MINIO_BUCKET
        self._initialized = False
        
        if MINIO_ENABLED and MINIO_URL and MINIO_ACCESS_KEY and MINIO_SECRET_KEY:
            try:
                # 解析URL，确保格式正确
                parsed_url = urlparse(MINIO_URL)
                endpoint = parsed_url.netloc or parsed_url.path
                
                # 创建MinIO客户端
                self._client = Minio(
                    endpoint=endpoint,
                    access_key=MINIO_ACCESS_KEY,
                    secret_key=MINIO_SECRET_KEY,
                    secure=MINIO_SECURE,
                    region=MINIO_REGION or None
                )
                
                # 检查并创建存储桶
                self._ensure_bucket_exists()
                
                self._initialized = True
                print(f"MinIO存储初始化成功: {endpoint}")
            except Exception as e:
                print(f"MinIO存储初始化失败: {str(e)}")
                self._client = None
        else:
            if not MINIO_ENABLED:
                print("MinIO存储未启用")
            else:
                print("MinIO配置不完整，请检查URL、访问密钥和秘密密钥")
    
    def _ensure_bucket_exists(self):
        """确保存储桶存在，不存在则创建"""
        try:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
                print(f"已创建存储桶: {self._bucket}")
            return True
        except S3Error as e:
            print(f"检查或创建存储桶时出错: {str(e)}")
            return False
    
    def is_available(self) -> bool:
        """检查MinIO存储是否可用"""
        return self._initialized and self._client is not None
    
    def upload_file(self, file_path: str, object_name: Optional[str] = None, content_type: Optional[str] = None) -> Tuple[bool, str]:
        """
        上传本地文件到MinIO
        
        Args:
            file_path: 本地文件路径
            object_name: MinIO中的对象名，如果不提供则使用文件名
            content_type: 内容类型，如果不提供则自动检测
            
        Returns:
            Tuple[bool, str]: (是否成功, 对象URL或错误信息)
        """
        if not self.is_available():
            return False, "MinIO存储不可用"
        
        try:
            # 如果没有提供对象名，使用文件名
            if not object_name:
                object_name = os.path.basename(file_path)
            
            # 上传文件
            self._client.fput_object(
                bucket_name=self._bucket,
                object_name=object_name,
                file_path=file_path,
                content_type=content_type
            )
            
            # 返回对象URL
            url = self.get_presigned_url(object_name)
            return True, url
        except Exception as e:
            print(f"上传文件失败 {file_path}: {str(e)}")
            return False, str(e)
    
    def upload_bytes(self, data: bytes, object_name: str, content_type: Optional[str] = None) -> Tuple[bool, str]:
        """
        上传二进制数据到MinIO
        
        Args:
            data: 二进制数据
            object_name: MinIO中的对象名
            content_type: 内容类型，如果不提供则为application/octet-stream
            
        Returns:
            Tuple[bool, str]: (是否成功, 对象URL或错误信息)
        """
        if not self.is_available():
            return False, "MinIO存储不可用"
        
        try:
            data_bytes_io = io.BytesIO(data)
            
            # 上传数据
            self._client.put_object(
                bucket_name=self._bucket,
                object_name=object_name,
                data=data_bytes_io,
                length=len(data),
                content_type=content_type or 'application/octet-stream'
            )
            
            # 返回对象URL
            url = self.get_presigned_url(object_name)
            return True, url
        except Exception as e:
            print(f"上传数据失败 {object_name}: {str(e)}")
            return False, str(e)
    
    def upload_text(self, text: str, object_name: str, content_type: str = 'text/plain; charset=utf-8') -> Tuple[bool, str]:
        """
        上传文本数据到MinIO
        
        Args:
            text: 文本内容
            object_name: MinIO中的对象名
            content_type: 内容类型，默认为text/plain; charset=utf-8
            
        Returns:
            Tuple[bool, str]: (是否成功, 对象URL或错误信息)
        """
        data = text.encode('utf-8')
        return self.upload_bytes(data, object_name, content_type)
    
    def download_file(self, object_name: str, file_path: str) -> bool:
        """
        从MinIO下载文件到本地
        
        Args:
            object_name: MinIO中的对象名
            file_path: 保存到本地的文件路径
            
        Returns:
            bool: 是否成功
        """
        if not self.is_available():
            return False
        
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            # 下载文件
            self._client.fget_object(
                bucket_name=self._bucket,
                object_name=object_name,
                file_path=file_path
            )
            return True
        except Exception as e:
            print(f"下载文件失败 {object_name}: {str(e)}")
            return False
    
    def download_bytes(self, object_name: str) -> Optional[bytes]:
        """
        从MinIO下载文件为二进制数据
        
        Args:
            object_name: MinIO中的对象名
            
        Returns:
            Optional[bytes]: 文件二进制数据，失败则返回None
        """
        if not self.is_available():
            return None
        
        try:
            # 下载对象
            response = self._client.get_object(
                bucket_name=self._bucket,
                object_name=object_name
            )
            
            # 读取所有数据
            data = response.read()
            response.close()
            response.release_conn()
            
            return data
        except Exception as e:
            print(f"下载文件数据失败 {object_name}: {str(e)}")
            return None
    
    def download_text(self, object_name: str, encoding: str = 'utf-8') -> Optional[str]:
        """
        从MinIO下载文件为文本
        
        Args:
            object_name: MinIO中的对象名
            encoding: 文本编码，默认为UTF-8
            
        Returns:
            Optional[str]: 文件文本内容，失败则返回None
        """
        data = self.download_bytes(object_name)
        if data is not None:
            try:
                return data.decode(encoding)
            except UnicodeDecodeError as e:
                print(f"解码文本失败 {object_name}: {str(e)}")
        return None
    
    def list_objects(self, prefix: str = "", recursive: bool = True) -> List[Dict[str, Any]]:
        """
        列出MinIO中的对象
        
        Args:
            prefix: 对象前缀
            recursive: 是否递归查找
            
        Returns:
            List[Dict[str, Any]]: 对象列表，每个对象包含元数据
        """
        if not self.is_available():
            return []
        
        try:
            objects = self._client.list_objects(
                bucket_name=self._bucket,
                prefix=prefix,
                recursive=recursive
            )
            
            result = []
            for obj in objects:
                result.append({
                    'name': obj.object_name,
                    'size': obj.size,
                    'last_modified': obj.last_modified
                })
            
            return result
        except Exception as e:
            print(f"列出对象失败 {prefix}: {str(e)}")
            return []
    
    def delete_file(self, object_name: str) -> bool:
        """
        删除MinIO中的文件
        
        Args:
            object_name: MinIO中的对象名
            
        Returns:
            bool: 是否成功
        """
        if not self.is_available():
            return False
        
        try:
            self._client.remove_object(
                bucket_name=self._bucket,
                object_name=object_name
            )
            return True
        except Exception as e:
            print(f"删除文件失败 {object_name}: {str(e)}")
            return False
    
    def get_presigned_url(self, object_name: str, expiry: int = 7*24*60*60) -> str:
        """
        获取文件的预签名URL，用于临时访问
        
        Args:
            object_name: MinIO中的对象名
            expiry: 有效期，默认7天（秒）
            
        Returns:
            str: 预签名URL
        """
        if not self.is_available():
            return ""
        
        try:
            url = self._client.presigned_get_object(
                bucket_name=self._bucket,
                object_name=object_name,
                expires=timedelta(seconds=expiry)
            )
            return url
        except Exception as e:
            print(f"获取预签名URL失败 {object_name}: {str(e)}")
            return ""

# 为不同类型文件生成对象名称的辅助函数
def get_pdf_object_name(task_id: str, filename: Optional[str] = None) -> str:
    """生成PDF文件的对象名称"""
    if filename:
        return f"{PDF_PREFIX}{task_id}/{filename}"
    return f"{PDF_PREFIX}{task_id}/script.pdf"

def get_image_object_name(script_task_id: str, image_filename: str) -> str:
    """生成图片文件的对象名称"""
    return f"{IMAGE_PREFIX}{script_task_id}/{image_filename}"

def get_state_object_name(task_id: str) -> str:
    """生成状态文件的对象名称"""
    return f"{STATE_PREFIX}{task_id}.pkl"

def get_content_object_name(task_id: str, episode: int) -> str:
    """生成内容文件的对象名称"""
    return f"{CONTENT_PREFIX}{task_id}_{episode}.txt"

# 异步操作函数
async def async_download_file(url: str, save_path: str) -> Optional[str]:
    """
    异步下载文件并保存到本地
    
    Args:
        url: 文件URL
        save_path: 保存路径
        
    Returns:
        Optional[str]: 保存路径，失败则返回None
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    # 确保目录存在
                    os.makedirs(os.path.dirname(save_path), exist_ok=True)
                    
                    # 保存文件
                    async with aiofiles.open(save_path, 'wb') as f:
                        await f.write(await response.read())
                    
                    return save_path
                else:
                    print(f"下载文件失败，状态码: {response.status}, URL: {url}")
                    return None
    except Exception as e:
        print(f"异步下载文件出错: {str(e)}, URL: {url}")
        return None

# 创建单例客户端实例
minio_client = MinioClient() 