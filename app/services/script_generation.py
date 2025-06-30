import asyncio
import uuid
import time
from typing import Dict, Any, Optional
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi import status

from app.models.schema import StreamScriptGenerationRequest
from app.core.generator import generate_character_and_directory
from app.core.generator_part2 import generate_episode
from app.core.config import API_KEY, API_URL
from app.utils.storage import save_generation_state, save_partial_content
from app.services.task_queue import active_streaming_tasks, format_sse_event


async def stream_generate_script_service(request: StreamScriptGenerationRequest) -> StreamingResponse:
    """流式生成脚本API服务"""
    task_id = str(uuid.uuid4())
    queue = asyncio.Queue()
    
    # 将任务添加到活跃任务字典中
    active_streaming_tasks[task_id] = {
        "is_active": True,
        "start_time": time.time(),
        "queue": queue,
        "type": "script_generation"
    }
    print(f"创建新的流式生成任务: {task_id}，当前活跃任务数: {len(active_streaming_tasks)}")
    
    async def event_generator():
        # 在函数内部定义变量
        initial_content = ""
        characters_directory_completed = False
        episode_generation_started = False
        current_episode = 1
        
        try:
            # 发送初始事件
            yield format_sse_event("task_id", {"task_id": task_id})
            yield format_sse_event("status", {"message": "正在生成角色表和目录..."})
            
            # 定义独立的异步回调函数
            async def initial_callback(chunk):
                print(f"收到角色表内容块: {len(chunk)}字符")
                await queue.put({"type": "initial_content_chunk", "content": chunk})
                return True
                
            async def episode_callback(chunk):
                print(f"收到剧集内容块: {len(chunk)}字符")
                await queue.put({"type": "episode_content_chunk", "content": chunk})
                return True
            
            # 创建任务
            print("开始生成角色表和目录...")
            initial_content_task = asyncio.create_task(
                generate_character_and_directory(
                request.genre,
                request.episodes,
                request.duration,
                request.characters,
                request.api_key or API_KEY,
                    request.api_url or API_URL,
                    content_callback=initial_callback
                )
            )
            
            # 处理队列中的事件
            while True:
                try:
                    # 等待队列项
                    item = await asyncio.wait_for(queue.get(), timeout=5.0)  # 增加超时时间
                    
                    # 检查是否是取消事件
                    if isinstance(item, dict) and item.get("type") == "cancel":
                        print(f"收到取消事件: {task_id}")
                        yield format_sse_event("canceled", {"message": "生成已被用户取消"})
                        break
                        
                    event_type = item["type"]
                    content = item["content"]
                    
                    # 处理不同类型的内容
                    if event_type == "initial_content_chunk":
                        # 记录内容
                        initial_content += content
                        # 发送内容块
                        yield format_sse_event("content_chunk", {
                            "content": content,
                            "is_complete": False
                        })
                    elif event_type == "episode_content_chunk":
                        # 发送内容块
                        yield format_sse_event("content_chunk", {
                            "content": content,
                            "is_complete": False
                        })
                    
                    queue.task_done()
                
                except asyncio.TimeoutError:
                    print("队列超时，检查任务状态...")

                    # 检查任务是否已被取消
                    if task_id in active_streaming_tasks and not active_streaming_tasks[task_id]["is_active"]:
                        print(f"检测到任务 {task_id} 已被取消")
                        yield format_sse_event("canceled", {"message": "生成已被用户取消"})
                        break
                    
                    # 检查角色表和目录生成任务
                    if not characters_directory_completed and initial_content_task.done():
                        try:
                            result = initial_content_task.result()
                            print(f"角色表和目录生成完成，长度: {len(result) if result else 0}字符")
                            
                            if result:
                                initial_content = result
                                save_generation_state(task_id, 0, initial_content)
                                
                                # 标记为已完成角色表和目录
                                characters_directory_completed = True
                                
                                # 发送状态更新
                                yield format_sse_event("status", {"message": f"正在生成第{current_episode}集..."})
                                yield format_sse_event("progress", {
                                    "current": current_episode,
                                    "total": request.episodes
                                })
                                
                                # 开始生成第一集
                                print(f"开始生成第{current_episode}集...")
                                episode_task = asyncio.create_task(
                                    generate_episode(
                                        current_episode,
                                        request.genre,
                                        request.episodes,
                                        request.duration,
                                        initial_content,
                                        request.api_key or API_KEY,
                                        request.api_url or API_URL,
                                        task_id,
                                        content_callback=episode_callback
                                    )
                                )
                                episode_generation_started = True
                            else:
                                print("角色表和目录生成结果为空")
                                yield format_sse_event("error", {"message": "角色表和目录生成失败"})
                                break
                        except Exception as e:
                            print(f"处理角色表和目录结果时出错: {str(e)}")
                            yield format_sse_event("error", {"message": f"生成内容出错: {str(e)}"})
                            break
                    
                    # 如果剧本生成已开始，检查是否完成
                    elif episode_generation_started and 'episode_task' in locals():
                        if episode_task.done():
                            try:
                                episode_content = episode_task.result()
                                print(f"第{current_episode}集生成完成，长度: {len(episode_content) if episode_content else 0}字符")
                                
                                # 检查是否有有效内容
                                if episode_content and len(episode_content) > 20:  # 至少要有一些实质内容
                                    # 保存生成的剧本
                                    initial_content += "\n\n" + episode_content
                                    save_generation_state(task_id, current_episode, initial_content)
                                    
                                    # 保存单集内容
                                    save_partial_content(task_id, current_episode, episode_content)
                                    
                                    # 增加集数
                                    current_episode += 1
                                    
                                    # 检查是否需要生成下一集
                                    if current_episode <= request.episodes:
                                        # 发送状态更新
                                        yield format_sse_event("status", {"message": f"正在生成第{current_episode}集..."})
                                        yield format_sse_event("progress", {
                                            "current": current_episode,
                                            "total": request.episodes
                                        })
                                        
                                        # 开始生成下一集
                                        print(f"开始生成第{current_episode}集...")
                                        episode_task = asyncio.create_task(
                                            generate_episode(
                                                current_episode,
                                                request.genre,
                                                request.episodes,
                                                request.duration,
                                                initial_content,
                                                request.api_key or API_KEY,
                                                request.api_url or API_URL,
                                                task_id,
                                                content_callback=episode_callback
                                            )
                                        )
                                    else:
                                        # 所有剧集都已生成完成
                                        yield format_sse_event("complete", {})
                                        break
                                else:
                                    print("生成的剧本内容为空或太短")
                                    yield format_sse_event("error", {"message": "生成的剧本内容为空或太短"})
                                    break
                            except Exception as e:
                                print(f"处理剧本生成结果时出错: {str(e)}")
                                yield format_sse_event("error", {"message": f"生成内容出错: {str(e)}"})
                                break
                    else:
                        # 检查任务是否运行太久
                        # 这里可以添加超时检查逻辑，例如使用asyncio.Task.get_coro()来获取任务创建时间
                        pass
                
                except Exception as e:
                    print(f"事件处理异常: {str(e)}")
                    yield format_sse_event("error", {"message": str(e)})
                    break
            
        except Exception as e:
            print(f"事件生成器主异常: {str(e)}")
            yield format_sse_event("error", {"message": str(e)})
        
        finally:
            # 清理任务
            if 'initial_content_task' in locals() and not initial_content_task.done():
                initial_content_task.cancel()
            if 'episode_task' in locals() and not episode_task.done():
                episode_task.cancel()
                
            # 从活跃任务列表中移除
            if task_id in active_streaming_tasks:
                del active_streaming_tasks[task_id]
                print(f"任务 {task_id} 已从活跃列表中移除")
    
    # 返回流式响应
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Transfer-Encoding": "chunked",
            "Keep-Alive": "timeout=600"  # 10分钟超时
        }
    ) 