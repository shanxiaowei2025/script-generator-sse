from fastapi import APIRouter, Request, BackgroundTasks, status
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
import asyncio
import json
import uuid
from typing import Optional, Callable, Awaitable, Any, Dict, List

from app.core.generator import generate_character_and_directory
from app.core.generator_part2 import generate_episode
from app.core.generator_part3 import resume_episode_generation
from app.utils.storage import (
    save_generation_state, 
    load_generation_state,
    find_latest_state_for_any_client,
    save_partial_content,
    get_partial_content
)
from app.utils.text_utils import extract_scene_prompts as extract_prompts, format_scene_prompts
from app.core.config import API_KEY, API_URL
from app.utils.runninghub_api import MAX_CONCURRENT_TASKS, call_runninghub_workflow, process_scene_prompts, query_task_status, query_task_result, wait_for_task_completion

# 定义请求模型
class StreamScriptGenerationRequest(BaseModel):
    genre: str
    duration: str
    episodes: int
    characters: List[str]
    api_key: Optional[str] = None
    api_url: Optional[str] = None

class ExtractScenePromptsRequest(BaseModel):
    task_id: str
    episode: Optional[int] = None

class RunningHubProcessRequest(BaseModel):
    task_id: str
    episode: Optional[int] = None

class RunningHubTaskStatusRequest(BaseModel):
    task_id: str

class RunningHubTaskResultRequest(BaseModel):
    task_id: str

# 创建路由器
router = APIRouter(tags=["Streaming API"])

# 流式生成状态跟踪
active_streaming_tasks = {}

@router.post("/stream/generate-script")
async def stream_generate_script(request: StreamScriptGenerationRequest):
    """
    流式生成脚本API端点
    返回 Server-Sent Events (SSE) 格式的流式响应
    """
    task_id = str(uuid.uuid4())
    queue = asyncio.Queue()
    
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

@router.delete("/stream/cancel/{task_id}")
async def cancel_streaming(task_id: str):
    """取消流式生成"""
    if task_id in active_streaming_tasks:
        active_streaming_tasks[task_id]["is_active"] = False
        return {"status": "canceled", "task_id": task_id}
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": f"任务 {task_id} 不在活跃流式生成中"}
    )

@router.post("/stream/extract-scene-prompts")
async def stream_extract_scene_prompts(request: ExtractScenePromptsRequest):
    """
    流式提取剧本中的画面描述词并返回
    使用task_id标识剧本
    """
    # 从请求体中获取task_id
    task_id = request.task_id
    
    # 检查存储中是否有对应剧本
    state = load_generation_state(task_id)
    if not state:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": f"未找到任务ID {task_id} 的剧本"}
        )
    
    async def event_generator():
        try:
            # 发送开始事件
            yield format_sse_event("status", {"message": "正在提取画面描述词..."})
            
            # 提取画面描述词
            script_text = state.get("full_script", "")
            prompts_dict = extract_prompts(script_text)
            
            # 格式化结果
            formatted_prompts = format_scene_prompts(prompts_dict, request.episode)
            
            # 如果指定了特定集数，只返回该集的内容
            if request.episode:
                result = {str(request.episode): formatted_prompts.get(str(request.episode), f"未找到第{request.episode}集的画面描述词")}
            else:
                result = formatted_prompts
            
            # 对于每个集数，分别发送事件
            for episode, content in result.items():
                yield format_sse_event("episode_prompts", {
                    "episode": episode,
                    "content": content
                })
                # 小延迟，帮助客户端处理
                await asyncio.sleep(0.1)
            
            # 发送完成事件
            yield format_sse_event("complete", {})
            
        except Exception as e:
            # 发送错误事件
            yield format_sse_event("error", {"message": f"提取画面描述词出错: {str(e)}"})
    
    # 返回流式响应
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # 禁用Nginx缓冲
        }
    )

@router.post("/stream/process-prompts-with-runninghub")
async def process_prompts_with_runninghub(request: RunningHubProcessRequest):
    """
    将剧本中提取的画面描述词发送到RunningHub API处理
    使用task_id标识剧本，采用队列方式处理提示词，实时返回每个任务的结果
    """
    # 从请求体中获取task_id
    task_id = request.task_id
    
    # 检查存储中是否有对应剧本
    state = load_generation_state(task_id)
    if not state:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": f"未找到任务ID {task_id} 的剧本"}
        )
    
    async def event_generator():
        try:
            # 发送开始事件
            yield format_sse_event("status", {"message": "正在提取画面描述词并发送到RunningHub..."})
            
            # 提取画面描述词
            script_text = state.get("full_script", "")
            prompts_dict = extract_prompts(script_text)
            
            # 打印详细提取信息
            print(f"提取到的画面描述词详情:")
            for episode, scenes in prompts_dict.items():
                scene_count = len(scenes)
                prompt_count = sum(len([p for p in prompts if p.replace('#', '').strip()]) for _, prompts in scenes.items())
                print(f"  第{episode}集: {scene_count}个场景, {prompt_count}个提示词")
            
            # 如果指定了特定集数，只处理该集的内容
            if request and request.episode is not None:
                specific_episode = request.episode
                if specific_episode in prompts_dict:
                    single_episode_dict = {specific_episode: prompts_dict[specific_episode]}
                    prompts_dict = single_episode_dict
                    print(f"只处理第{specific_episode}集的画面描述词")
                else:
                    print(f"错误: 未找到第{specific_episode}集的画面描述词")
                    yield format_sse_event("error", {"message": f"未找到第{specific_episode}集的画面描述词"})
                    return
            
            # 创建任务队列
            task_queue = asyncio.Queue()
            # 创建事件队列
            event_queue = asyncio.Queue()
            
            # 任务进度跟踪
            total_tasks = 0
            completed_tasks = 0
            active_tasks = []
            
            # 结果字典，用于存储中间结果
            results = {}
            
            # 计算提示词总数并将任务加入队列
            for episode, scenes in prompts_dict.items():
                # 格式化集数键为"第X集"
                episode_key = f"第{episode}集" if not str(episode).startswith("第") else str(episode)
                results[episode_key] = {}
                
                for scene, prompts in scenes.items():
                    # 格式化场景键为"场次X-X"
                    scene_key = f"场次{scene}" if not str(scene).startswith("场次") else str(scene)
                    results[episode_key][scene_key] = {}
                    
                    # 添加有效提示词到队列
                    for idx, prompt in enumerate(prompts):
                        clean_prompt = prompt.replace('#', '').strip()
                        if clean_prompt:
                            total_tasks += 1
                            await task_queue.put({
                                "episode": episode,
                                "episode_key": episode_key,
                                "scene": scene,
                                "scene_key": scene_key,
                                "prompt_index": idx,
                                "prompt": clean_prompt
                            })
            
            # 发送状态更新
            yield format_sse_event("status", {
                "message": f"检测到{total_tasks}个提示词，将使用队列方式处理(最大并发{MAX_CONCURRENT_TASKS}个)...",
                "total_prompts": total_tasks
            })
            
            # 处理单个任务的协程（注意：没有yield，而是使用队列）
            async def process_task(task):
                try:
                    episode_key = task["episode_key"]
                    scene_key = task["scene_key"]
                    prompt_index = task["prompt_index"]
                    prompt = task["prompt"]
                    
                    print(f"开始处理任务: {episode_key} {scene_key} 提示词{prompt_index}")
                    
                    # 调用API
                    create_result = await call_runninghub_workflow(prompt)
                    print(f"创建任务结果: {create_result}")
                    
                    # 提取runninghub_task_id
                    runninghub_task_id = None
                    if create_result and isinstance(create_result, dict):
                        if "data" in create_result:
                            data = create_result.get("data", {})
                            # data可能是字典也可能是字符串
                            if isinstance(data, dict):
                                runninghub_task_id = data.get("taskId")
                            elif isinstance(data, str) and data.isdigit():
                                # 有时API可能直接返回数字ID作为字符串
                                runninghub_task_id = data
                        # 有些API响应可能直接将taskId放在顶层
                        elif "taskId" in create_result:
                            runninghub_task_id = create_result.get("taskId")
                            
                        print(f"提取的RunningHub任务ID: {runninghub_task_id}")
                    
                    # 如果没有提取到有效的taskId，则设置为失败
                    if not runninghub_task_id:
                        print(f"未能从创建结果中提取有效的任务ID: {create_result}")
                    
                    # 创建任务结果
                    task_result = {
                        "prompt": prompt,
                        "create_result": create_result,
                        "task_id": runninghub_task_id,
                        "status_result": None,
                        "final_result": None,
                        "status": "CREATED" if runninghub_task_id else "FAILED"
                    }
                    
                    # 保存任务结果
                    if episode_key not in results:
                        results[episode_key] = {}
                    if scene_key not in results[episode_key]:
                        results[episode_key][scene_key] = {}
                    
                    # 确保prompt_index是字符串，避免在字典操作中出现类型问题
                    str_prompt_index = str(prompt_index)
                    results[episode_key][scene_key][str_prompt_index] = task_result
                    
                    # 将创建事件发送到队列
                    create_event = format_sse_event("task_created", {
                        "episode": episode_key,
                        "scene": scene_key,
                        "prompt_index": prompt_index,
                        "task_id": runninghub_task_id
                    })
                    print(f"发送创建事件: {episode_key} {scene_key} 提示词{prompt_index}")
                    await event_queue.put(create_event)
                    
                    # 如果任务创建成功，等待完成
                    if runninghub_task_id:
                        print(f"等待任务完成: {runninghub_task_id}")
                        final_status, final_result = await wait_for_task_completion(runninghub_task_id)
                        print(f"任务完成: {runninghub_task_id}, 状态: {final_status}")
                        
                        # 更新任务结果
                        task_result["status_result"] = final_status
                        task_result["final_result"] = final_result
                        task_result["status"] = "SUCCESS" if final_status in ["SUCCESS", "FINISHED", "COMPLETE", "COMPLETED"] else "FAILED"
                        
                        # 保存更新后的结果
                        results[episode_key][scene_key][str_prompt_index] = task_result
                        
                        # 将完成事件发送到队列
                        complete_event = format_sse_event("task_completed", {
                            "episode": episode_key,
                            "scene": scene_key,
                            "prompt_index": prompt_index,
                            "task_id": runninghub_task_id,
                            "status": task_result["status"],
                            "result": {
                                "episode": episode_key,
                                "results": {
                                    scene_key: {
                                        str_prompt_index: task_result
                                    }
                                }
                            }
                        })
                        print(f"发送完成事件: {episode_key} {scene_key} 提示词{prompt_index}")
                        await event_queue.put(complete_event)
                    
                    # 返回结果，而不是yield
                    return task_result
                    
                except Exception as e:
                    print(f"处理任务时出错: {str(e)}")
                    # 处理错误...
                    # 将错误事件发送到队列
                    error_event = format_sse_event("task_error", {
                        "episode": task["episode_key"],
                        "scene": task["scene_key"],
                        "prompt_index": str(task["prompt_index"]),  # 确保是字符串
                        "error": str(e)
                    })
                    await event_queue.put(error_event)
                    
                    return {"error": str(e), "status": "ERROR"}
            
            # Worker协程（不使用yield）
            async def worker():
                nonlocal completed_tasks
                
                while True:
                    task = None
                    try:
                        # 获取任务
                        task = await task_queue.get()
                        
                        # 添加到活动任务
                        active_tasks.append(task)
                        
                        # 发送状态更新到队列
                        status_event = format_sse_event("status", {
                            "message": f"正在处理 第{task['episode']}集 场次{task['scene']} 提示词{task['prompt_index']}...",
                            "active_tasks": [{"episode": str(t["episode"]), "scene": str(t["scene"]), "prompt_index": str(t["prompt_index"])} for t in active_tasks],
                            "completed": completed_tasks,
                            "total": total_tasks,
                            "queue_size": task_queue.qsize()
                        })
                        await event_queue.put(status_event)
                        
                        # 处理任务
                        result = await process_task(task)
                        print(f"任务处理完成: {task['episode_key']}, 场次{task['scene_key']}, 提示词{task['prompt_index']}, 状态: {result.get('status', 'UNKNOWN')}")
                        
                        # 更新计数
                        completed_tasks += 1
                        
                        # 发送进度更新
                        progress_event = format_sse_event("progress", {
                            "completed": completed_tasks,
                            "total": total_tasks,
                            "percentage": int(completed_tasks * 100 / total_tasks)
                        })
                        await event_queue.put(progress_event)
                        
                        print(f"队列状态: 已完成={completed_tasks}, 总数={total_tasks}, 剩余={task_queue.qsize()}, 活动任务数={len(active_tasks)}")
                        
                        # 成功处理，标记任务完成
                        task_queue.task_done()
                        
                    except asyncio.CancelledError:
                        # 工作器被取消，不调用task_done()
                        print(f"工作器被取消")
                        # 如果没有获取任务就被取消，不要调用task_done()
                        break
                        
                    except Exception as e:
                        print(f"Worker异常: {str(e)}")
                        import traceback
                        print(traceback.format_exc())
                        
                    finally:
                        # 从活动任务中移除
                        if task and task in active_tasks:
                            active_tasks.remove(task)
            
            # 启动worker任务
            workers = []
            for i in range(MAX_CONCURRENT_TASKS):
                print(f"启动worker {i+1}")
                worker_task = asyncio.create_task(worker())
                workers.append(worker_task)
            
            # 从事件队列读取并yield事件
            try:
                # 设置是否已发送完成事件的标志
                complete_sent = False
                
                while True:
                    try:
                        # 等待事件，较短的超时确保响应性
                        event = await asyncio.wait_for(event_queue.get(), timeout=0.5)
                        print(f"从事件队列获取到事件，准备发送")
                        yield event
                        event_queue.task_done()
                        
                    except asyncio.TimeoutError:
                        # 检查是否所有任务都已完成
                        if task_queue.empty() and not active_tasks and completed_tasks == total_tasks:
                            if not complete_sent:
                                print("所有任务完成，发送完成事件")
                                yield format_sse_event("complete", {})
                                complete_sent = True
                                # 不立即退出循环，给一些时间让最后的事件被处理
                                print("等待3秒确保所有事件处理完成")
                                await asyncio.sleep(3)
                                break
                            else:
                                # 最后再等待一会，确保所有事件都已处理
                                await asyncio.sleep(1)
                                break
                            
                        # 发送更新状态
                        if not task_queue.empty() or active_tasks:
                            print(f"等待任务完成: 已完成={completed_tasks}/{total_tasks}, 队列剩余={task_queue.qsize()}, 活动任务={len(active_tasks)}")
            
            except Exception as e:
                print(f"事件处理循环异常: {str(e)}")
                import traceback
                print(traceback.format_exc())
                
            finally:
                # 取消所有worker
                print("准备取消所有worker")
                for worker_task in workers:
                    if not worker_task.done():
                        print(f"取消worker任务")
                        worker_task.cancel()
                
                # 等待所有worker正常结束
                try:
                    print("等待所有worker结束...")
                    await asyncio.sleep(1)  # 给worker一些时间来处理取消
                except Exception as e:
                    print(f"等待worker结束时异常: {str(e)}")
                
                # 如果还没有发送完成事件，确保发送
                if not complete_sent:
                    print("发送最终完成事件")
                    yield format_sse_event("complete", {})
                
                print("事件生成器结束")
            
        except Exception as e:
            # 发送错误
            import traceback
            error_msg = f"处理画面描述词出错: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            yield format_sse_event("error", {"message": error_msg})
    
    # 返回流式响应
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

@router.post("/runninghub/task-status")
async def get_runninghub_task_status(request: RunningHubTaskStatusRequest):
    """
    查询RunningHub任务状态
    
    Args:
        request: 包含task_id的请求对象
        
    Returns:
        任务状态信息
    """
    if not request.task_id:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "缺少任务ID"}
        )
    
    try:
        # 调用RunningHub API查询任务状态
        status_result = await query_task_status(request.task_id)
    
        # 否则只返回状态信息
        return {
            "task_id": request.task_id,
            "status": status_result
        }
    
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": f"查询任务状态出错: {str(e)}"}
        )

@router.post("/runninghub/task-result")
async def get_runninghub_task_result(request: RunningHubTaskResultRequest):
    """
    查询RunningHub任务结果
    
    Args:
        request: 包含task_id的请求对象
        
    Returns:
        任务结果信息
    """
    if not request.task_id:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "缺少任务ID"}
        )
    
    try:
        # 调用RunningHub API查询任务结果
        result = await query_task_result(request.task_id)
        
        return {
            "task_id": request.task_id,
            "result": result
        }
    
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": f"查询任务结果出错: {str(e)}"}
        )

def format_sse_event(event_type: str, data: Any) -> str:
    """格式化SSE事件"""
    json_data = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {json_data}\n\n" 


