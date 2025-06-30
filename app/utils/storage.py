import os
import pickle
from typing import Dict, Any, Optional
import json
from datetime import datetime
import io
from app.core.config import GENERATION_STATES_DIR, PARTIAL_CONTENTS_DIR, MINIO_ENABLED, SAVE_FILES_LOCALLY

# 内存中的状态存储
generation_states = {}
episode_partial_contents = {}  # 保存每个任务每一集的部分生成内容

def save_generation_state(task_id, current_episode, full_script):
    """保存生成状态到内存和文件"""
    # 确保script_content是UTF-8编码的字符串
    if isinstance(full_script, bytes):
        full_script = full_script.decode('utf-8')
        
    state = {
        "current_episode": current_episode,
        "full_script": full_script,
        "timestamp": datetime.now().isoformat()
    }
    
    # 保存到内存
    generation_states[task_id] = state
    
    # 序列化状态数据（用于保存或上传）
    state_data = pickle.dumps(state)
    
    # 如果需要保存到本地
    if SAVE_FILES_LOCALLY:
        # 确保目录存在
        os.makedirs(GENERATION_STATES_DIR, exist_ok=True)
        
        # 保存到文件系统
        state_file = os.path.join(GENERATION_STATES_DIR, f"{task_id}.pkl")
        with open(state_file, "wb") as f:
            f.write(state_data)
    
    # 如果启用了MinIO，同时保存到MinIO
    if MINIO_ENABLED:
        try:
            from app.utils.minio_storage import minio_client, get_state_object_name
            
            if minio_client.is_available():
                # 上传到MinIO
                object_name = get_state_object_name(task_id)
                success, url = minio_client.upload_bytes(state_data, object_name, 'application/octet-stream')
                
                if success:
                    print(f"已将状态数据上传到MinIO: {object_name}")
                else:
                    print(f"上传状态数据到MinIO失败: {url}")
        except Exception as e:
            print(f"MinIO存储状态数据失败: {str(e)}")
        
    return state

def load_generation_state(task_id):
    """加载生成状态"""
    # 先尝试从内存加载
    if task_id in generation_states:
        state = generation_states[task_id]
        # 确保script是UTF-8字符串
        if "full_script" in state and isinstance(state["full_script"], bytes):
            state["full_script"] = state["full_script"].decode('utf-8')
        return state
    
    # 内存中没有，尝试从文件加载
    state_file = os.path.join(GENERATION_STATES_DIR, f"{task_id}.pkl")
    state = None
    
    # 1. 尝试从本地文件加载
    try:
        if os.path.exists(state_file):
            with open(state_file, "rb") as f:
                state = pickle.load(f)
                # 确保script是UTF-8字符串
                if "full_script" in state and isinstance(state["full_script"], bytes):
                    state["full_script"] = state["full_script"].decode('utf-8')
                generation_states[task_id] = state
    except Exception as e:
        print(f"从本地加载状态出错: {str(e)}")
    
    # 2. 如果本地加载失败且启用了MinIO，尝试从MinIO加载
    if state is None and MINIO_ENABLED:
        try:
            from app.utils.minio_storage import minio_client, get_state_object_name
            
            if minio_client.is_available():
                # 从MinIO下载状态数据
                object_name = get_state_object_name(task_id)
                state_data = minio_client.download_bytes(object_name)
                
                if state_data:
                    # 反序列化状态数据
                    state = pickle.loads(state_data)
                    
                    # 确保script是UTF-8字符串
                    if "full_script" in state and isinstance(state["full_script"], bytes):
                        state["full_script"] = state["full_script"].decode('utf-8')
                    
                    # 保存到内存和本地
                    generation_states[task_id] = state
                    
                    # 同时保存到本地文件系统，确保两端同步
                    try:
                        with open(state_file, "wb") as f:
                            pickle.dump(state, f)
                        print(f"已将MinIO状态数据同步到本地: {state_file}")
                    except Exception as e:
                        print(f"同步MinIO状态数据到本地失败: {str(e)}")
                else:
                    print(f"从MinIO加载状态数据失败: {object_name}")
        except Exception as e:
            print(f"从MinIO加载状态数据出错: {str(e)}")
    
    return state

def find_latest_state_for_any_client():
    """查找所有任务中最新的状态文件"""
    try:
        latest_time = 0
        latest_file = None
        
        os.makedirs(GENERATION_STATES_DIR, exist_ok=True)
        
        # 先从本地文件系统查找
        for file in os.listdir(GENERATION_STATES_DIR):
            if file.endswith(".pkl"):
                file_path = os.path.join(GENERATION_STATES_DIR, file)
                file_time = os.path.getmtime(file_path)
                if file_time > latest_time:
                    latest_time = file_time
                    latest_file = file_path
        
        # 如果找到了本地文件，加载并返回
        if latest_file:
            with open(latest_file, "rb") as f:
                state = pickle.load(f)
                # 确保script是UTF-8字符串
                if "full_script" in state and isinstance(state["full_script"], bytes):
                    state["full_script"] = state["full_script"].decode('utf-8')
                return state
        
        # 如果本地没有找到且启用了MinIO，尝试从MinIO查找
        if MINIO_ENABLED and not latest_file:
            try:
                from app.utils.minio_storage import minio_client
                
                if minio_client.is_available():
                    # 列出MinIO中的状态文件
                    objects = minio_client.list_objects("states/")
                    
                    # 查找最新的
                    latest_obj = None
                    latest_modified = None
                    
                    for obj in objects:
                        if obj["name"].endswith(".pkl"):
                            modified = obj["last_modified"]
                            if latest_modified is None or modified > latest_modified:
                                latest_modified = modified
                                latest_obj = obj
                    
                    # 如果找到了，下载并返回
                    if latest_obj:
                        task_id = os.path.basename(latest_obj["name"]).split('.')[0]
                        return load_generation_state(task_id)
            except Exception as e:
                print(f"从MinIO查找最新状态出错: {str(e)}")
    except Exception as e:
        print(f"查找最新状态出错: {str(e)}")
    
    return None

def save_partial_content(task_id, episode, content):
    """保存部分生成内容到文件和内存"""
    from app.core.config import SAVE_FILES_LOCALLY
    
    # 确保content是UTF-8编码的字符串
    if isinstance(content, bytes):
        content = content.decode('utf-8')
        
    # 保存到内存
    key = f"{task_id}_{episode}"
    episode_partial_contents[key] = content
    
    # 创建元数据
    meta = {
        "task_id": task_id,
        "episode": episode,
        "timestamp": datetime.now().isoformat(),
        "size": len(content)
    }
    
    # 如果需要保存到本地
    if SAVE_FILES_LOCALLY:
        # 确保目录存在
        os.makedirs(PARTIAL_CONTENTS_DIR, exist_ok=True)
        
        # 保存内容到文件
        file_path = os.path.join(PARTIAL_CONTENTS_DIR, f"{task_id}_{episode}.txt")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
            
        # 保存元数据到文件
        meta_path = os.path.join(PARTIAL_CONTENTS_DIR, f"{task_id}_{episode}_meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
    
    # 如果启用了MinIO，也保存到MinIO
    if MINIO_ENABLED:
        try:
            from app.utils.minio_storage import minio_client, get_content_object_name
            
            if minio_client.is_available():
                # 上传内容文件
                object_name = get_content_object_name(task_id, episode)
                success, url = minio_client.upload_text(content, object_name, 'text/plain; charset=utf-8')
                
                if success:
                    print(f"已将部分内容上传到MinIO: {object_name}")
                    
                    # 上传元数据
                    meta_object = f"{object_name}_meta.json"
                    meta_success, _ = minio_client.upload_text(
                        json.dumps(meta, ensure_ascii=False), 
                        meta_object, 
                        'application/json; charset=utf-8'
                    )
                    
                    if not meta_success:
                        print(f"上传部分内容元数据到MinIO失败: {meta_object}")
                else:
                    print(f"上传部分内容到MinIO失败: {url}")
        except Exception as e:
            print(f"MinIO存储部分内容失败: {str(e)}")

def get_partial_content(task_id, episode):
    """获取部分生成内容"""
    # 先尝试从内存获取
    key = f"{task_id}_{episode}"
    if key in episode_partial_contents:
        content = episode_partial_contents[key]
        # 确保返回UTF-8字符串
        if isinstance(content, bytes):
            content = content.decode('utf-8')
        return content
    
    # 再尝试从文件系统获取
    file_path = os.path.join(PARTIAL_CONTENTS_DIR, f"{task_id}_{episode}.txt")
    content = None
    
    # 1. 尝试从本地文件加载
    try:
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
                episode_partial_contents[key] = content
    except Exception as e:
        print(f"从本地读取部分内容出错: {str(e)}")
    
    # 2. 如果本地加载失败且启用了MinIO，尝试从MinIO加载
    if content is None and MINIO_ENABLED:
        try:
            from app.utils.minio_storage import minio_client, get_content_object_name
            
            if minio_client.is_available():
                # 从MinIO下载内容
                object_name = get_content_object_name(task_id, episode)
                content = minio_client.download_text(object_name)
                
                if content:
                    # 保存到内存
                    episode_partial_contents[key] = content
                    
                    # 同时保存到本地文件系统，确保两端同步
                    try:
                        with open(file_path, "w", encoding="utf-8") as f:
                            f.write(content)
                        print(f"已将MinIO部分内容同步到本地: {file_path}")
                    except Exception as e:
                        print(f"同步MinIO部分内容到本地失败: {str(e)}")
                else:
                    print(f"从MinIO加载部分内容失败: {object_name}")
        except Exception as e:
            print(f"从MinIO加载部分内容出错: {str(e)}")
    
    return content

def get_client_generation_status(task_id: str) -> Optional[Dict[str, Any]]:
    """获取客户端生成状态"""
    state = load_generation_state(task_id)
    if not state:
        return None
    
    return {
        "task_id": task_id,
        "episodes_completed": state.get("current_episode", 0),
        "status": "completed" if state.get("current_episode", 0) > 0 else "not_started"
    } 