import os
import pickle
from typing import Dict, Any, Optional
import json
from datetime import datetime
from app.core.config import GENERATION_STATES_DIR, PARTIAL_CONTENTS_DIR

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
    
    # 确保目录存在
    os.makedirs(GENERATION_STATES_DIR, exist_ok=True)
    
    # 保存到文件系统
    state_file = os.path.join(GENERATION_STATES_DIR, f"{task_id}.pkl")
    with open(state_file, "wb") as f:
        pickle.dump(state, f)
        
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
    
    try:
        if os.path.exists(state_file):
            with open(state_file, "rb") as f:
                state = pickle.load(f)
                # 确保script是UTF-8字符串
                if "full_script" in state and isinstance(state["full_script"], bytes):
                    state["full_script"] = state["full_script"].decode('utf-8')
                generation_states[task_id] = state
                return state
    except Exception as e:
        print(f"加载状态出错: {str(e)}")
    
    return None

def find_latest_state_for_any_client():
    """查找所有任务中最新的状态文件"""
    try:
        latest_time = 0
        latest_file = None
        
        os.makedirs(GENERATION_STATES_DIR, exist_ok=True)
        
        for file in os.listdir(GENERATION_STATES_DIR):
            if file.endswith(".pkl"):
                file_path = os.path.join(GENERATION_STATES_DIR, file)
                file_time = os.path.getmtime(file_path)
                if file_time > latest_time:
                    latest_time = file_time
                    latest_file = file_path
        
        if latest_file:
            with open(latest_file, "rb") as f:
                state = pickle.load(f)
                # 确保script是UTF-8字符串
                if "full_script" in state and isinstance(state["full_script"], bytes):
                    state["full_script"] = state["full_script"].decode('utf-8')
                return state
    except Exception as e:
        print(f"查找最新状态出错: {str(e)}")
    
    return None

def save_partial_content(task_id, episode, content):
    """保存部分生成内容到文件和内存"""
    # 确保content是UTF-8编码的字符串
    if isinstance(content, bytes):
        content = content.decode('utf-8')
        
    # 保存到内存
    key = f"{task_id}_{episode}"
    episode_partial_contents[key] = content
    
    # 确保目录存在
    os.makedirs(PARTIAL_CONTENTS_DIR, exist_ok=True)
    
    # 保存到文件
    file_path = os.path.join(PARTIAL_CONTENTS_DIR, f"{task_id}_{episode}.txt")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
        
    # 同时保存元数据
    meta_path = os.path.join(PARTIAL_CONTENTS_DIR, f"{task_id}_{episode}_meta.json")
    meta = {
        "task_id": task_id,
        "episode": episode,
        "timestamp": datetime.now().isoformat(),
        "size": len(content)
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)

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
    
    try:
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
                episode_partial_contents[key] = content
                return content
    except Exception as e:
        print(f"读取部分内容出错: {str(e)}")
    
    return None

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