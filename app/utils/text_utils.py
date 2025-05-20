import re

def extract_title_and_directory(full_script: str) -> str:
    """提取剧名和目录
    
    从完整脚本内容中提取剧名、角色表和目录，用于后续生成提示。
    
    Args:
        full_script: 完整剧本内容
        
    Returns:
        提取出的剧名、角色表和目录
    """
    # 提取结果
    result = ""
    
    # 1. 提取剧名 (通常是《...》格式)
    title_match = re.search(r'《.*?》', full_script[:1000])
    if title_match:
        title = title_match.group(0)
        result += f"剧名：{title}\n\n"
    
    # 2. 提取角色表 (表格形式)
    # 查找角色表开始标记
    char_table_start = full_script.find("| 角色名称 ")
    if char_table_start != -1:
        # 从角色表开始找到下一个空行作为结束
        char_table_end = full_script.find("\n\n", char_table_start)
        if char_table_end != -1:
            char_table = full_script[char_table_start:char_table_end]
            result += f"【角色表】\n{char_table}\n\n"
    
    # 3. 提取分集目录 (使用"短剧分集目录"和"第XX集"模式)
    directory_start = full_script.find("短剧分集目录")
    if directory_start == -1:
        # 尝试其他可能的目录标记
        directory_start = full_script.find("分集目录")
    
    if directory_start != -1:
        # 从目录开始，提取所有"第XX集"行
        directory_section = full_script[directory_start:directory_start + 2000]  # 假设目录不会超过2000字符
        
        # 拆分目录部分的文本为行
        lines = directory_section.split('\n')
        directory_lines = []
        
        # 添加目录标题
        directory_lines.append("【分集目录】")
        
        # 提取所有"第XX集"格式的行
        for line in lines:
            if re.match(r'^第\d+集', line.strip()):
                directory_lines.append(line.strip())
        
        if directory_lines:
            result += '\n'.join(directory_lines) + "\n\n"
    
    # 如果没有找到任何内容，则回退到简单方法
    if not result:
        print("警告: 无法精确提取剧名、角色和目录，使用简单截取方法")
        return full_script[:5000]
    
    print(f"成功提取剧名、角色表和目录，总计{len(result)}字符")
    return result 

def extract_scene_prompts(script_text):
    """
    从剧本文本中提取画面描述词
    
    Args:
        script_text (str): 剧本完整文本
        
    Returns:
        dict: 按集数和场次组织的画面描述词字典
    """
    if not script_text:
        return {}
    
    # 结果字典，格式为 {集数: {场次: [描述词1, 描述词2, ...]}}
    prompts_by_episode = {}
    
    # 当前处理的集数和场次
    current_episode = None
    current_scene = None
    
    # 按行分割剧本
    lines = script_text.split('\n')
    
    # 首先尝试从剧本开头识别第一集
    for i, line in enumerate(lines):
        line = line.strip()
        
        # 检测剧名行
        if ('剧名：' in line or '# 剧名：' in line) and current_episode is None:
            # 尝试从后续几行中找到集数
            for j in range(i+1, min(i+10, len(lines))):
                next_line = lines[j].strip()
                # 处理"集数：第X集"或"第X集"格式
                if ('集数：' in next_line or '集' in next_line) and '第' in next_line:
                    episode_match = re.search(r'第(\d+)集', next_line)
                    if episode_match:
                        current_episode = int(episode_match.group(1))
                        # print(f"在剧本开头找到集数: {current_episode}")
                        if current_episode not in prompts_by_episode:
                            prompts_by_episode[current_episode] = {}
                        break
    
    # 如果没有识别到集数，默认为第1集
    if current_episode is None:
        print("未能识别集数，默认为第1集")
        current_episode = 1
        prompts_by_episode[current_episode] = {}
    
    # 处理场次和描述词
    for line in lines:
        line = line.strip()
        
        # 检查是否是新的集
        if ('集数：第' in line or line.startswith('第')) and '集' in line:
            episode_match = re.search(r'第(\d+)集', line)
            if episode_match:
                current_episode = int(episode_match.group(1))
                # print(f"在处理过程中发现新集数: {current_episode}")
                if current_episode not in prompts_by_episode:
                    prompts_by_episode[current_episode] = {}
        
        # 检测场次 - 同时支持"场次X-X："和"### 场次X-X："格式
        elif ('场次' in line) and ('：' in line or ':' in line):
            scene_match = re.search(r'(?:###\s*)?场次(\d+-\d+)[：:]', line)
            if scene_match:
                current_scene = scene_match.group(1)
                
                # 从场次编号中提取集数（如场次5-3中的5）
                scene_ep_match = re.match(r'(\d+)-\d+', current_scene)
                if scene_ep_match:
                    scene_episode = int(scene_ep_match.group(1))
                    # 如果场次编号中的集数与当前集数不同，更新当前集数
                    if scene_episode != current_episode:
                        current_episode = scene_episode
                        # print(f"从场次编号提取到新集数: {current_episode}")
                        if current_episode not in prompts_by_episode:
                            prompts_by_episode[current_episode] = {}
                
                # 规范化场次编号 - 确保场次编号第一部分与当前集数一致
                scene_parts = current_scene.split('-')
                if len(scene_parts) == 2 and int(scene_parts[0]) != current_episode:
                    # 修正场次编号的第一部分为当前集数
                    current_scene = f"{current_episode}-{scene_parts[1]}"
                    print(f"规范化场次编号: 原编号={scene_match.group(1)}, 新编号={current_scene} (属于第{current_episode}集)")
                else:
                    print(f"处理场次: {current_scene} (属于第{current_episode}集)")
                
                if current_scene not in prompts_by_episode[current_episode]:
                    prompts_by_episode[current_episode][current_scene] = []
        
        # 提取画面描述词
        elif line.startswith('#') and current_scene is not None:
            # 排除不是真正画面描述词的特殊行
            if not any(excluded in line for excluded in ['# 剧名', '#剧名', '# 集数', '#集数']):
                # 确保当前集数和场次的列表存在
                if current_episode not in prompts_by_episode:
                    prompts_by_episode[current_episode] = {}
                if current_scene not in prompts_by_episode[current_episode]:
                    prompts_by_episode[current_episode][current_scene] = []
                
                # 保留完整的描述词，包括前导的#符号
                prompt = line
                prompts_by_episode[current_episode][current_scene].append(prompt)
                print(f"提取到画面描述词: 第{current_episode}集 场次{current_scene} - {prompt[:50]}...")
            else:
                print(f"排除特殊行: {line[:30]}...")
                pass
    
    # 添加统计信息，便于调试
    total_prompts = 0
    for episode, scenes in prompts_by_episode.items():
        ep_count = 0
        print(f"第{episode}集包含场次:")
        for scene, prompts in scenes.items():
            print(f"  场次{scene}: {len(prompts)}个提示词")
            ep_count += len(prompts)
            total_prompts += len(prompts)
        print(f"第{episode}集共有{ep_count}个提示词")
    print(f"总共提取到{total_prompts}个提示词")
    
    # 对每集的场次进行重新编号，确保从1开始连续编号
    renumbered_prompts = {}
    for episode, scenes in prompts_by_episode.items():
        renumbered_prompts[episode] = {}
        
        # 直接复制场次，保留原始场次编号的第二部分
        for original_scene in scenes.keys():
            # 如果场次包含"-"，确保第一部分与集数一致
            if '-' in original_scene:
                scene_parts = original_scene.split('-')
                if len(scene_parts) == 2 and int(scene_parts[0]) != episode:
                    # 只修改第一部分为当前集数，保留第二部分原始编号
                    new_scene = f"{episode}-{scene_parts[1]}"
                    print(f"规范化场次编号: 原编号={original_scene}, 新编号={new_scene} (属于第{episode}集)")
                else:
                    new_scene = original_scene
            else:
                # 如果没有"-"格式，直接使用原编号
                new_scene = original_scene
                
            # 复制原场次的提示词到规范化的场次编号下
            renumbered_prompts[episode][new_scene] = scenes[original_scene]
    
    return renumbered_prompts

def format_scene_prompts(prompts_dict, specific_episode=None):
    """
    将提取的画面描述词格式化为指定的输出格式
    
    Args:
        prompts_dict (dict): 按集数和场次组织的画面描述词字典
        specific_episode (int, optional): 指定要格式化的集数，None表示格式化所有集
        
    Returns:
        dict: 按集数组织的格式化文本，key为集数字符串，value为该集的格式化文本
    """
    if not prompts_dict:
        return {"1": "未找到画面描述词"}
    
    result = {}
    
    # 确定要处理的集数
    episodes_to_format = [specific_episode] if specific_episode is not None else sorted(prompts_dict.keys())
    
    # 按集数格式化
    for episode in episodes_to_format:
        if episode not in prompts_dict:
            result[str(episode)] = f"未找到第{episode}集的画面描述词"
            continue
            
        output = []
        output.append(f"第{episode}集：")
        
        # 按场次排序
        for scene in sorted(prompts_dict[episode].keys(), key=lambda x: [int(i) for i in x.split('-')]):
            output.append(f"场次{scene}：")
            
            # 添加该场次的所有画面描述词
            for prompt in prompts_dict[episode][scene]:
                output.append(prompt)
            
            output.append("")  # 空行分隔场次
        
        # 将该集的输出合并为字符串
        result[str(episode)] = "\n".join(output)
    
    return result 