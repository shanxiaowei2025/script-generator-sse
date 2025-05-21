#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
迁移工具：将本地存储的文件迁移到MinIO对象存储服务
"""

import os
import sys
import argparse
import time
import asyncio
from typing import Dict, List, Any, Tuple, Optional
import glob
from pathlib import Path
import pickle
import json
from tqdm import tqdm
import mimetypes
import random

# 添加项目根目录到系统路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.config import (
    GENERATION_STATES_DIR,
    PARTIAL_CONTENTS_DIR,
    PDFS_DIR,
    IMAGES_DIR,
    MINIO_URL,
    MINIO_ACCESS_KEY,
    MINIO_SECRET_KEY,
    MINIO_BUCKET,
    MINIO_SECURE
)
from app.utils.minio_storage import (
    minio_client,
    get_pdf_object_name,
    get_image_object_name,
    get_state_object_name,
    get_content_object_name
)

# 定义颜色
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
ENDC = "\033[0m"

def print_color(color, text):
    """打印彩色文本"""
    print(f"{color}{text}{ENDC}")

def ensure_minio_config():
    """确保MinIO配置有效"""
    if not MINIO_URL:
        print_color(RED, "错误: MinIO URL未配置")
        print("请在.env文件中添加MINIO_URL")
        return False
    
    if not MINIO_ACCESS_KEY or not MINIO_SECRET_KEY:
        print_color(RED, "错误: MinIO访问密钥或秘密密钥未配置")
        print("请在.env文件中添加MINIO_ACCESS_KEY和MINIO_SECRET_KEY")
        return False
    
    if not MINIO_BUCKET:
        print_color(RED, "错误: MinIO存储桶名称未配置")
        print("请在.env文件中添加MINIO_BUCKET")
        return False
    
    return True

def scan_files() -> Dict[str, List[str]]:
    """扫描本地文件，返回分类后的文件列表"""
    result = {
        "pdfs": [],
        "images": [],
        "states": [],
        "contents": []
    }
    
    # 扫描PDF文件
    pdf_pattern = os.path.join(PDFS_DIR, "**", "*.pdf")
    result["pdfs"] = glob.glob(pdf_pattern, recursive=True)
    
    # 扫描图片文件 (支持多种格式)
    for ext in ["png", "jpg", "jpeg", "gif", "webp"]:
        image_pattern = os.path.join(IMAGES_DIR, "**", f"*.{ext}")
        result["images"].extend(glob.glob(image_pattern, recursive=True))
    
    # 扫描状态文件
    state_pattern = os.path.join(GENERATION_STATES_DIR, "*.pkl")
    result["states"] = glob.glob(state_pattern)
    
    # 扫描内容文件
    content_pattern = os.path.join(PARTIAL_CONTENTS_DIR, "*.txt")
    result["contents"] = glob.glob(content_pattern)
    
    # 过滤出非元数据文件
    result["contents"] = [f for f in result["contents"] if not f.endswith("_meta.txt")]
    
    return result

def upload_pdf_file(file_path: str) -> Tuple[bool, str]:
    """上传PDF文件到MinIO"""
    try:
        # 跳过JSON文件
        if file_path.endswith('.json'):
            return True, f"已跳过JSON文件: {file_path}"
            
        # 提取任务ID和文件名
        rel_path = os.path.relpath(file_path, PDFS_DIR)
        parts = rel_path.split(os.sep)
        
        if len(parts) >= 2:
            # 如果文件在子目录中，使用子目录名作为任务ID
            task_id = parts[0]
            filename = parts[-1]
        else:
            # 如果文件在根目录，从文件名中提取任务ID
            filename = parts[0]
            task_id = filename.split("_")[1] if "_" in filename else "unknown"
        
        # 生成对象名
        object_name = get_pdf_object_name(task_id, filename)
        
        # 上传文件
        success, url = minio_client.upload_file(file_path, object_name, 'application/pdf')
        return success, object_name if success else url
    except Exception as e:
        return False, str(e)

def upload_image_file(file_path: str) -> Tuple[bool, str]:
    """上传图片文件到MinIO"""
    try:
        # 提取任务ID和文件名
        rel_path = os.path.relpath(file_path, IMAGES_DIR)
        parts = rel_path.split(os.sep)
        
        if len(parts) >= 2:
            # 如果图片在子目录中，使用子目录名作为脚本任务ID
            script_task_id = parts[0]
            image_filename = parts[-1]
        else:
            # 如果图片在根目录，无法确定任务ID，使用默认值
            script_task_id = "unknown"
            image_filename = parts[0]
        
        # 生成对象名
        object_name = get_image_object_name(script_task_id, image_filename)
        
        # 检测内容类型
        content_type, _ = mimetypes.guess_type(file_path)
        if not content_type:
            content_type = 'image/png'  # 默认图片类型
        
        # 上传文件
        success, url = minio_client.upload_file(file_path, object_name, content_type)
        return success, object_name if success else url
    except Exception as e:
        return False, str(e)

def upload_state_file(file_path: str) -> Tuple[bool, str]:
    """上传状态文件到MinIO"""
    try:
        # 提取任务ID
        task_id = os.path.basename(file_path).split('.')[0]
        
        # 生成对象名
        object_name = get_state_object_name(task_id)
        
        # 上传文件
        success, url = minio_client.upload_file(file_path, object_name, 'application/octet-stream')
        return success, object_name if success else url
    except Exception as e:
        return False, str(e)

def upload_content_file(file_path: str) -> Tuple[bool, str]:
    """上传内容文件到MinIO"""
    try:
        # 跳过JSON文件
        if file_path.endswith('.json'):
            return True, f"已跳过JSON文件: {file_path}"
            
        # 提取任务ID和集数
        basename = os.path.basename(file_path)
        if "_" in basename:
            parts = basename.split('_')
            task_id = parts[0]
            episode = int(parts[1].split('.')[0])
        else:
            task_id = basename.split('.')[0]
            episode = 1  # 默认第1集
        
        # 读取文本内容
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 生成对象名
        object_name = get_content_object_name(task_id, episode)
        
        # 上传文本 - 明确指定内容类型为纯文本
        success, url = minio_client.upload_text(content, object_name, 'text/plain')
        
        return success, object_name if success else url
    except Exception as e:
        return False, str(e)

async def migrate_files(files: Dict[str, List[str]], args):
    """迁移所有文件到MinIO"""
    # 初始化计数器
    total_files = sum(len(files[key]) for key in files)
    uploaded = 0
    failed = 0
    skipped = 0
    skipped_json = 0
    
    # 创建进度条
    progress = tqdm(total=total_files, desc="迁移进度", unit="文件")
    
    # 随机打乱文件顺序，以便均匀处理
    for key in files:
        random.shuffle(files[key])
    
    # 处理PDF文件
    if args.pdf and files["pdfs"]:
        print_color(BLUE, f"\n开始迁移PDF文件 ({len(files['pdfs'])}个)...")
        for pdf_file in files["pdfs"]:
            success, result = upload_pdf_file(pdf_file)
            if success:
                if "跳过JSON文件" in result:
                    skipped_json += 1
                    if args.verbose:
                        print_color(YELLOW, f"⚠ 跳过: {os.path.basename(pdf_file)}")
                else:
                    uploaded += 1
                    if args.verbose:
                        print_color(GREEN, f"✓ PDF: {os.path.basename(pdf_file)} -> {result}")
            else:
                failed += 1
                print_color(RED, f"✗ PDF: {os.path.basename(pdf_file)} 失败: {result}")
            progress.update(1)
    else:
        skipped += len(files["pdfs"])
        progress.update(len(files["pdfs"]))
    
    # 处理图片文件
    if args.images and files["images"]:
        print_color(BLUE, f"\n开始迁移图片文件 ({len(files['images'])}个)...")
        for image_file in files["images"]:
            success, result = upload_image_file(image_file)
            if success:
                uploaded += 1
                if args.verbose:
                    print_color(GREEN, f"✓ 图片: {os.path.basename(image_file)} -> {result}")
            else:
                failed += 1
                print_color(RED, f"✗ 图片: {os.path.basename(image_file)} 失败: {result}")
            progress.update(1)
    else:
        skipped += len(files["images"])
        progress.update(len(files["images"]))
    
    # 处理状态文件
    if args.states and files["states"]:
        print_color(BLUE, f"\n开始迁移状态文件 ({len(files['states'])}个)...")
        for state_file in files["states"]:
            success, result = upload_state_file(state_file)
            if success:
                uploaded += 1
                if args.verbose:
                    print_color(GREEN, f"✓ 状态: {os.path.basename(state_file)} -> {result}")
            else:
                failed += 1
                print_color(RED, f"✗ 状态: {os.path.basename(state_file)} 失败: {result}")
            progress.update(1)
    else:
        skipped += len(files["states"])
        progress.update(len(files["states"]))
    
    # 处理内容文件
    if args.contents and files["contents"]:
        print_color(BLUE, f"\n开始迁移内容文件 ({len(files['contents'])}个)...")
        for content_file in files["contents"]:
            success, result = upload_content_file(content_file)
            if success:
                if "跳过JSON文件" in result:
                    skipped_json += 1
                    if args.verbose:
                        print_color(YELLOW, f"⚠ 跳过: {os.path.basename(content_file)}")
                else:
                    uploaded += 1
                    if args.verbose:
                        print_color(GREEN, f"✓ 内容: {os.path.basename(content_file)} -> {result}")
            else:
                failed += 1
                print_color(RED, f"✗ 内容: {os.path.basename(content_file)} 失败: {result}")
            progress.update(1)
    else:
        skipped += len(files["contents"])
        progress.update(len(files["contents"]))
    
    # 关闭进度条
    progress.close()
    
    # 返回统计结果
    return {
        "total": total_files,
        "uploaded": uploaded,
        "failed": failed,
        "skipped": skipped,
        "skipped_json": skipped_json
    }

async def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="将本地文件迁移到MinIO对象存储")
    parser.add_argument("--pdf", action="store_true", help="迁移PDF文件")
    parser.add_argument("--images", action="store_true", help="迁移图片文件")
    parser.add_argument("--states", action="store_true", help="迁移状态文件")
    parser.add_argument("--contents", action="store_true", help="迁移内容文件")
    parser.add_argument("--all", action="store_true", help="迁移所有文件")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细输出")
    
    args = parser.parse_args()
    
    # 如果没有指定任何参数，默认迁移所有文件
    if not (args.pdf or args.images or args.states or args.contents or args.all):
        args.all = True
    
    # 如果指定了--all，设置所有文件类型标志
    if args.all:
        args.pdf = True
        args.images = True
        args.states = True
        args.contents = True
    
    # 检查MinIO配置
    if not ensure_minio_config():
        sys.exit(1)
    
    # 检查MinIO连接
    if not minio_client.is_available():
        print_color(RED, "错误: 无法连接到MinIO服务器")
        sys.exit(1)
    
    print_color(GREEN, f"已成功连接到MinIO: {MINIO_URL}")
    print_color(GREEN, f"存储桶: {MINIO_BUCKET}")
    
    # 扫描本地文件
    print_color(BLUE, "正在扫描本地文件...")
    files = scan_files()
    
    total_count = sum(len(files[key]) for key in files)
    print_color(GREEN, f"已扫描到 {total_count} 个文件:")
    print(f"  PDF文件: {len(files['pdfs'])}个")
    print(f"  图片文件: {len(files['images'])}个")
    print(f"  状态文件: {len(files['states'])}个")
    print(f"  内容文件: {len(files['contents'])}个")
    
    if total_count == 0:
        print_color(YELLOW, "没有找到需要迁移的文件")
        return
    
    # 确认是否继续
    if not args.verbose:  # 非详细模式需要确认
        confirm = input("\n是否开始迁移文件到MinIO? [y/N] ")
        if confirm.lower() != 'y':
            print("已取消迁移")
            return
    
    # 开始迁移
    start_time = time.time()
    print_color(BLUE, "\n开始迁移文件到MinIO...")
    
    stats = await migrate_files(files, args)
    
    # 显示统计信息
    elapsed = time.time() - start_time
    print_color(GREEN, f"\n迁移完成! 耗时: {elapsed:.2f}秒")
    print(f"总文件数: {stats['total']}")
    print(f"成功上传: {stats['uploaded']}")
    print(f"上传失败: {stats['failed']}")
    print(f"已跳过: {stats['skipped']}")
    print(f"跳过JSON文件: {stats['skipped_json']}")
    
    if stats['failed'] > 0:
        print_color(YELLOW, "\n注意: 有些文件上传失败，请查看上面的错误信息并重试")

if __name__ == "__main__":
    asyncio.run(main()) 