import os
import io
import pickle
from typing import List, Dict, Any, Tuple, Optional
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak, Table, TableStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_CENTER
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
import re
from app.core.config import IMAGES_DIR
from PIL import Image as PILImage
import markdown
import html2text
from bs4 import BeautifulSoup
import json
import time
import tempfile
import shutil
import uuid

from app.utils.minio_storage import IMAGE_PREFIX

# 注册中文字体
try:
    # 尝试注册思源黑体 (Source Han Sans)
    font_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '../static/fonts/SourceHanSansSC-Regular.otf')
    if os.path.exists(font_path):
        pdfmetrics.registerFont(TTFont('SourceHanSans', font_path))
        font_name = 'SourceHanSans'
    else:
        # 尝试注册系统中文字体
        if os.path.exists('/System/Library/Fonts/PingFang.ttc'):  # macOS
            pdfmetrics.registerFont(TTFont('PingFang', '/System/Library/Fonts/PingFang.ttc'))
            font_name = 'PingFang'
        elif os.path.exists('/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc'):  # Linux
            pdfmetrics.registerFont(TTFont('NotoSans', '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc'))
            font_name = 'NotoSans'
        else:
            # 尝试使用ReportLab内置的中文字体
            try:
                pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
                font_name = 'STSong-Light'
            except:
                font_name = 'Helvetica'  # 默认回退
except Exception as e:
    print(f"字体注册失败: {str(e)}")
    font_name = 'Helvetica'  # 默认回退到无中文支持的字体

# 创建中文样式，确保编码支持
styles = getSampleStyleSheet()
styles.add(ParagraphStyle(
    name='ChineseTitle',
    fontName=font_name,
    fontSize=16,
    leading=20,
    alignment=TA_CENTER,  # 居中
    spaceAfter=12,
    encoding='utf-8'  # 明确指定UTF-8编码
))
styles.add(ParagraphStyle(
    name='ChineseHeading',
    fontName=font_name,
    fontSize=12,
    leading=16,
    spaceAfter=6,
    encoding='utf-8'  # 明确指定UTF-8编码
))
styles.add(ParagraphStyle(
    name='ChineseNormal',
    fontName=font_name,
    fontSize=10,
    leading=14,
    spaceBefore=2,
    spaceAfter=2,
    encoding='utf-8'  # 明确指定UTF-8编码
))
styles.add(ParagraphStyle(
    name='ChinesePrompt',
    fontName=font_name,
    fontSize=10,
    leading=14,
    leftIndent=10,
    textColor=colors.darkblue,
    encoding='utf-8'  # 明确指定UTF-8编码
))
styles.add(ParagraphStyle(
    name='WarningStyle',
    fontName=font_name,
    fontSize=10,
    leading=14,
    textColor=colors.red,
    encoding='utf-8'
))
styles.add(ParagraphStyle(
    name='WarningDetailStyle',
    fontName=font_name,
    fontSize=8,
    leading=12,
    textColor=colors.grey,
    encoding='utf-8'
))
styles.add(ParagraphStyle(
    name='ChineseScene',
    fontName=font_name,
    fontSize=12,
    leading=16,
    spaceAfter=6,
    bold=True,
    encoding='utf-8'  # 明确指定UTF-8编码
))
styles.add(ParagraphStyle(
    name='ChineseListItem',
    fontName=font_name,
    fontSize=10,
    leading=14,
    leftIndent=20,
    bulletIndent=10,
    spaceBefore=2,
    spaceAfter=2,
    encoding='utf-8'
))
styles.add(ParagraphStyle(
    name='ChineseLink',
    fontName=font_name,
    fontSize=10,
    leading=14,
    textColor=colors.blue,
    encoding='utf-8'
))
styles.add(ParagraphStyle(
    name='ChineseCodeBlock',
    fontName=font_name,
    fontSize=9,
    leading=12,
    leftIndent=20,
    rightIndent=20,
    backColor=colors.lightgrey,
    borderWidth=1,
    borderColor=colors.grey,
    borderPadding=5,
    spaceBefore=5,
    spaceAfter=5,
    encoding='utf-8'
))
styles.add(ParagraphStyle(
    name='TableCell',
    fontName=font_name,
    fontSize=9,
    leading=12,
    textColor=colors.black,
    encoding='utf-8'
))
styles.add(ParagraphStyle(
    name='TableHeader',
    fontName=font_name,
    fontSize=10,
    leading=14,
    alignment=1,  # 居中
    textColor=colors.black,
    encoding='utf-8'
))
styles.add(ParagraphStyle(
    name='ImageTitle',
    fontName=font_name,
    fontSize=9,
    leading=12,
    alignment=1,  # 居中
    textColor=colors.darkgrey,
    encoding='utf-8'
))
styles.add(ParagraphStyle(
    name='ChineseHeading2',
    fontName=font_name,
    fontSize=14,
    leading=18,
    spaceBefore=6,
    spaceAfter=3,
    textColor=colors.navy,
    encoding='utf-8'
))
styles.add(ParagraphStyle(
    name='ChineseHeading3',
    fontName=font_name,
    fontSize=12,
    leading=16,
    spaceBefore=4,
    spaceAfter=2,
    textColor=colors.darkblue,
    encoding='utf-8'
))

# 添加加载剧本文件的函数
def load_script_from_pkl(script_file_path: str) -> Tuple[str, Optional[str]]:
    """
    从pickle文件中加载剧本内容
    
    Args:
        script_file_path: pickle文件路径
        
    Returns:
        Tuple[str, Optional[str]]: 剧本内容和当前集数
    """
    try:
        with open(script_file_path, 'rb') as f:
            data = pickle.load(f)
        
        if isinstance(data, dict) and 'full_script' in data:
            script_content = data['full_script']
            current_episode = data.get('current_episode')
            return script_content, current_episode
        else:
            raise ValueError(f"无法从文件中提取剧本内容: {script_file_path}")
    except Exception as e:
        print(f"加载剧本文件失败: {str(e)}")
        raise

# 优化解析Markdown函数，增强格式支持
def parse_markdown(text):
    """将Markdown文本转换为HTML，支持表格、代码块、粗体等扩展功能"""
    # 预处理表格标记，确保能够正确识别
    if '|' in text:
        # 检查是否是表格行
        stripped = text.strip()
        if stripped.startswith('|') and stripped.endswith('|'):
            # 检查是否是虚线分隔行（表头和内容的分隔）
            if any(cell.strip().startswith('-') and set(cell.strip()) == {'-'} for cell in stripped.strip('|').split('|')):
                # 识别为分隔行，返回一个特殊标记的HTML，后续可以识别处理
                return '<table><tbody><tr class="separator-row"><td>SEPARATOR_ROW</td></tr></tbody></table>'
            # 其他普通表格行
            else:
                rows = []
                # 分割文本行
                cells = [cell.strip() for cell in stripped.strip('|').split('|')]
                # 创建表格HTML
                html_rows = '<tr>' + ''.join([f'<td>{cell}</td>' for cell in cells]) + '</tr>'
                table_html = f'<table><tbody>{html_rows}</tbody></table>'
                return table_html
    
    # 预处理Markdown标题，确保能正确识别各级标题
    if text.startswith('#'):
        hash_count = 0
        for char in text:
            if char == '#':
                hash_count += 1
            else:
                break
        
        if hash_count >= 1 and hash_count <= 6:
            # 截取标题内容
            title_content = text[hash_count:].strip()
            
            # 修改：不再跳过特定标题
            # 直接返回对应级别的HTML标题
            return f'<h{hash_count}>{title_content}</h{hash_count}>'
            
    # 预处理粗体和斜体
    if '**' in text or '__' in text:
        # 替换Markdown粗体为HTML标签
        text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'__(.*?)__', r'<strong>\1</strong>', text)
    
    if '*' in text and not '**' in text:
        # 替换Markdown斜体为HTML标签
        text = re.sub(r'\*(.*?)\*', r'<em>\1</em>', text)
    
    if '_' in text and not '__' in text:
        # 替换Markdown斜体为HTML标签
        text = re.sub(r'_(.*?)_', r'<em>\1</em>', text)
    
    # 预处理行内代码
    if '`' in text and not text.startswith('```'):
        # 替换Markdown行内代码为HTML标签
        text = re.sub(r'`(.*?)`', r'<code>\1</code>', text)
    
    # 使用完整的markdown解析，包含更多扩展
    extensions = [
        'tables', 
        'fenced_code', 
        'codehilite', 
        'nl2br',  # 转换换行为<br>标签
        'sane_lists',  # 更好的列表支持
        'smarty',  # 智能引号和破折号
        'toc'  # 支持目录
    ]
    return markdown.markdown(text, extensions=extensions)

def html_to_plain(html):
    """将HTML转换为纯文本，保留基本格式"""
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.body_width = 0  # 不自动换行
    return h.handle(html)

# 修改表格处理函数，改进单行表格的处理
def process_markdown_table(html_content):
    """从HTML中提取表格并转换为ReportLab的Table对象"""
    soup = BeautifulSoup(html_content, 'html.parser')
    tables = []
    
    for table_html in soup.find_all('table'):
        # 检查表格标题，保留所有表格包括目录表格
        table_caption = table_html.find('caption')
        if table_caption:
            print(f"表格标题: {table_caption.get_text().strip()}")
            
        # 获取所有行
        rows = []
        
        # 检查是否有分隔行（特殊标记）
        separator_row = table_html.select_one('tr.separator-row')
        if separator_row and separator_row.get_text().strip() == 'SEPARATOR_ROW':
            # 这是一个分隔行，不添加内容，只在后续的表格样式中使用
            continue
        
        # 处理表头
        thead = table_html.find('thead')
        if thead:
            for tr in thead.find_all('tr'):
                row = []
                for th in tr.find_all(['th']):
                    row.append(Paragraph(th.get_text().strip(), ParagraphStyle(
                        name='TableHeader',
                        fontName=font_name,
                        fontSize=10,
                        leading=12,
                        alignment=1,  # 居中
                        textColor=colors.black,
                        encoding='utf-8'
                    )))
                if row:
                    rows.append(row)
        
        # 处理表格主体
        tbody = table_html.find('tbody')
        if tbody:
            for tr in tbody.find_all('tr'):
                # 跳过分隔行
                if 'separator-row' in tr.get('class', []):
                    continue
                    
                row = []
                for td in tr.find_all(['td']):
                    row.append(Paragraph(td.get_text().strip(), ParagraphStyle(
                        name='TableCell',
                        fontName=font_name,
                        fontSize=9,
                        leading=12,
                        encoding='utf-8'
                    )))
                if row:
                    rows.append(row)
        
        # 如果没有明确的thead或tbody，直接处理所有tr
        if not rows:
            for tr in table_html.find_all('tr'):
                # 跳过分隔行
                if 'separator-row' in tr.get('class', []):
                    continue
                    
                row = []
                for td in tr.find_all(['td', 'th']):
                    is_header = td.name == 'th'
                    style = ParagraphStyle(
                        name='TableHeader' if is_header else 'TableCell',
                        fontName=font_name,
                        fontSize=10 if is_header else 9,
                        leading=12,
                        alignment=1 if is_header else 0,  # 表头居中，单元格左对齐
                        textColor=colors.black,
                        encoding='utf-8'
                    )
                    row.append(Paragraph(td.get_text().strip(), style))
                if row:
                    rows.append(row)
        
        # 创建表格
        if rows:
            # 确保表格有内容
            if all(len(row) > 0 for row in rows):
                # 创建表格对象
                table = Table(rows)
                
                # 设置表格样式
                style = TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),  # 表头背景
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),  # 表头文字颜色
                    ('ALIGN', (0, 0), (-1, 0), 'CENTER'),  # 表头居中
                    ('FONTNAME', (0, 0), (-1, 0), font_name),  # 表头字体
                    ('FONTSIZE', (0, 0), (-1, 0), 10),  # 表头字号
                    ('BOTTOMPADDING', (0, 0), (-1, 0), 6),  # 表头底部间距
                    ('BACKGROUND', (0, 1), (-1, -1), colors.white),  # 表格主体背景
                    ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),  # 表格主体文字颜色
                    ('ALIGN', (0, 1), (-1, -1), 'LEFT'),  # 表格主体左对齐
                    ('FONTNAME', (0, 1), (-1, -1), font_name),  # 表格主体字体
                    ('FONTSIZE', (0, 1), (-1, -1), 9),  # 表格主体字号
                    ('TOPPADDING', (0, 1), (-1, -1), 3),  # 表格主体顶部间距
                    ('BOTTOMPADDING', (0, 1), (-1, -1), 3),  # 表格主体底部间距
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),  # 网格线
                    # 添加表头下方的粗分隔线
                    ('LINEBELOW', (0, 0), (-1, 0), 1.5, colors.black),  # 表头下方的粗线
                ])
                table.setStyle(style)
                tables.append(table)
    
    return tables

async def generate_script_pdf(
    script_content: str, 
    image_data: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]], 
    output_path: str = None,
    task_id: str = None,
    progress_callback = None
) -> bytes:
    """
    生成包含剧本和图片的PDF文件
    
    Args:
        script_content: 剧本文本内容
        image_data: 包含图片相关信息的字典，格式为 {episode: {scene: {prompt_idx: {prompt}}}}
        output_path: 输出文件路径，不指定则返回PDF字节数据
        task_id: 任务ID，用于查找本地图片
        progress_callback: 进度回调函数
        
    Returns:
        bytes: PDF文件的字节数据
    """
    # 确保script_content是UTF-8编码的字符串
    if isinstance(script_content, bytes):
        script_content = script_content.decode('utf-8')
    
    # 创建临时目录用于存放压缩图片
    temp_dir = os.path.join(tempfile.gettempdir(), f"pdf_temp_{uuid.uuid4().hex}")
    os.makedirs(temp_dir, exist_ok=True)
    print(f"创建临时目录用于图片压缩: {temp_dir}")
    
    try:
        # 预处理剧本内容，删除所有剧名行和目录部分中的无关内容
        print("预处理剧本内容，删除所有剧名行和目录部分中的无关内容...")
        lines = script_content.split('\n')
        
        # 处理剧本内容，删除所有剧名行和目录部分中的无关内容
        preprocessed_lines = []
        
        # 查找目录部分
        directory_index = -1
        first_episode_index = -1
        directory_end_index = -1
        
        # 目录相关的关键词
        directory_keywords = ["目录", "分集目录", "剧集目录", "短剧分集目录"]
        
        # 首先查找目录开始位置
        for i, line in enumerate(lines):
            if any(keyword in line for keyword in directory_keywords):
                directory_index = i
                print(f"找到目录行，行号: {directory_index+1}")
                break
        
        # 然后查找第一集的开始位置
        if directory_index >= 0:
            for i in range(directory_index + 1, len(lines)):
                if "集数：第" in lines[i] or (lines[i].strip().startswith("场次") and "：" in lines[i]):
                    first_episode_index = i
                    print(f"找到第一集开始行，行号: {first_episode_index+1}")
                    break
        
        # 查找目录的末尾（通常是分集列表之后出现其他内容）
        if directory_index >= 0:
            directory_end_found = False
            for i in range(directory_index + 1, len(lines)):
                # 检查当前行是否是分集标题（例如"第XX集：XXX"）
                is_episode_title = bool(re.search(r'第\d+集[:：]', lines[i].strip()))
                
                # 如果不是分集标题，并且找到了非空行，可能是目录的结束
                if not is_episode_title and lines[i].strip() and i > directory_index + 2:
                    # 检查是否是特殊的分隔符或其他非内容行
                    if not lines[i].strip().startswith("--") and not lines[i].strip().startswith("=="):
                        directory_end_index = i
                        directory_end_found = True
                        print(f"找到目录末尾行，行号: {directory_end_index+1}")
                        break
                
                # 如果发现了"AI短剧生成方案"，也认为是目录结束
                if "AI短剧生成方案" in lines[i] or "生成方案" in lines[i]:
                    directory_end_index = i
                    directory_end_found = True
                    print(f"找到目录结束标记（生成方案），行号: {directory_end_index+1}")
                    break
            
            # 如果没找到明确的目录结束，但找到了第一集开始，就使用第一集开始前的位置
            if not directory_end_found and first_episode_index > 0:
                directory_end_index = first_episode_index - 1
                print(f"使用第一集开始前的位置作为目录结束，行号: {directory_end_index+1}")
        
        # 处理每一行
        for i, line in enumerate(lines):
            line_text = line.strip()
            
            # 检查是否是剧名行（各种格式）
            is_title_line = False
            if (line_text.startswith("剧名：") or 
                line_text.startswith("剧名:") or 
                line_text.startswith("# 剧名") or
                ("剧名：" in line_text) or 
                ("剧名:" in line_text) or
                ("剧名" in line_text and ("《" in line_text or "》" in line_text))):
                is_title_line = True
            
            # 检查是否是"AI短剧生成方案"行
            is_ai_plan_line = "AI短剧生成方案" in line_text or "生成方案" in line_text
            
            # 不再检查和跳过目录与第一集之间的内容，我们希望保留目录内容
            
            # 处理行
            if is_title_line:
                # 删除所有剧名行
                print(f"删除剧名行: {line_text} (行号:{i+1})")
                # 跳过这一行，不添加到处理后的内容中
            elif is_ai_plan_line:
                # 删除AI方案行
                print(f"删除AI方案行: {line_text} (行号:{i+1})")
                # 跳过这一行，不添加
            else:
                # 保留所有内容，包括目录
                preprocessed_lines.append(line)
        
        # 更新处理后的内容
        print(f"预处理完成，删除了所有剧名行")
        script_content = '\n'.join(preprocessed_lines)
        
        # 更新进度
        if progress_callback:
            progress_callback(5, "创建PDF文档...")
        
        # 创建PDF文档
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer if output_path is None else output_path,
            pagesize=A4,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=72,
            encoding='utf-8'  # 明确指定UTF-8编码
        )
        
        # 存储PDF元素
        elements = []
        
        # 提取剧名，首先尝试从"# 《...》"这样的格式提取
        title_match = None
        for line in script_content.split('\n')[:20]:  # 只检查前20行
            if line.strip().startswith("# 《") and "》" in line:
                # 从"# 《剧名》"格式提取
                title = re.search(r'#\s*《(.+?)》', line).group(1)
                title_match = True
                break
            
        # 如果没找到，尝试从第X集标题中提取
        if not title_match:
            for line in script_content.split('\n')[:50]:  # 检查前50行
                ep_match = re.search(r'第\d+集[：:]\s*(.+?)$', line)
                if ep_match:
                    title = ep_match.group(1).strip()
                    title_match = True
                    break
                
        # 如果上面都没找到，回退到原来的提取方法
        if not title_match:
            title_match = re.search(r'《(.+?)》', script_content[:1000])
            title = f"{title_match.group(1)}" if title_match else "剧本"
        
        elements.append(Paragraph(f"《{title}》- 剧本与场景图", styles['ChineseTitle']))
        elements.append(Spacer(1, 0.25 * inch))
        
        # 更新进度
        if progress_callback:
            progress_callback(10, "检查图片资源...")
        
        # 验证图片目录是否存在
        if task_id:
            image_dir = os.path.join(IMAGES_DIR, task_id)
            if not os.path.exists(image_dir):
                print(f"警告: 图片目录不存在: {image_dir}")
        else:
            print("警告: 未提供task_id，将无法找到本地图片")
            image_dir = None
        
        # 更新进度
        if progress_callback:
            progress_callback(15, "处理剧本内容...")
        
        # 处理剧本内容
        lines = script_content.split('\n')
        current_episode = None
        current_scene = None
        # 修改场次格式正则表达式，使其更灵活匹配不同格式
        scene_format = re.compile(r'场次(\d+(?:-\d+)?)[：:：\s]?')
        # 修改集数正则表达式，支持更多格式
        episode_format = re.compile(r'第(\d+)集')
        episode_number_format = re.compile(r'集数[：:：]\s*第(\d+)集')
        
        # 代码块处理相关变量
        in_code_block = False
        code_block_lines = []
        code_block_language = ""
        
        # 表格处理相关变量
        in_table = False
        table_rows = []
        
        # 估算总行数和处理进度
        total_lines = len(lines)
        last_progress = 15
        
        # 遍历剧本的每一行
        i = 0
        while i < len(lines):
            # 定期更新进度
            if i % 100 == 0 and progress_callback:
                # 计算处理进度，从15%到90%
                current_progress = 15 + int(75 * i / total_lines)
                if current_progress > last_progress:
                    progress_callback(current_progress, f"处理剧本内容... ({i}/{total_lines}行)")
                    last_progress = current_progress
            
            line = lines[i].strip()
            
            # 跳过空行，但在代码块和表格内不跳过
            if not line and not in_code_block and not in_table:
                i += 1
                continue
                
            # 检测代码块开始和结束
            if line.startswith("```"):
                if not in_code_block:  # 代码块开始
                    in_code_block = True
                    code_block_lines = []
                    # 提取语言信息
                    code_block_language = line[3:].strip()
                else:  # 代码块结束
                    in_code_block = False
                    # 处理整个代码块
                    if code_block_lines:
                        code_text = "\n".join(code_block_lines)
                        code_para = Paragraph(
                            f"<pre>{code_text}</pre>", 
                            styles['ChineseCodeBlock']
                        )
                        elements.append(code_para)
                i += 1
                continue
                
            # 在代码块内，收集代码行
            if in_code_block:
                code_block_lines.append(line)
                i += 1
                continue
                
            # 表格处理逻辑
            if '|' in line and line.strip().startswith('|') and line.strip().endswith('|'):
                # 如果这是第一行表格
                if not in_table:
                    in_table = True
                    table_rows = []
                
                # 检查是否是分隔行(全是连字符的行)
                is_separator_line = False
                cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
                if cells and all(set(cell) == {'-'} for cell in cells if cell):
                    is_separator_line = True
                    # 如果已经有表头行，添加一条分隔线
                    if table_rows:
                        # 不添加新行，但记录当前已经处理过分隔行
                        # 稍后在样式中添加粗分隔线
                        i += 1
                        continue
                
                # 解析普通表格行
                if not is_separator_line:
                    # 将单元格转换为Paragraph对象
                    row = [Paragraph(cell, styles['TableCell'] if table_rows else styles['TableHeader']) for cell in cells if cell]
                    if row:
                        table_rows.append(row)
                
                # 检查下一行是否还是表格
                next_line_idx = i + 1
                if next_line_idx < len(lines):
                    next_line = lines[next_line_idx].strip()
                    # 如果下一行不是表格行或者是空行，结束表格处理
                    if not next_line or not ('|' in next_line and next_line.startswith('|') and next_line.endswith('|')):
                        # 创建并添加表格
                        if table_rows:
                            table = Table(table_rows)
                            # 为表格添加样式
                            style = TableStyle([
                                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                                ('FONTNAME', (0, 0), (-1, -1), font_name),
                                ('FONTSIZE', (0, 0), (-1, -1), 9),
                                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                                ('TOPPADDING', (0, 0), (-1, -1), 3),
                                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                            ])
                            # 如果有表头（第一行）设置特殊样式
                            if len(table_rows) > 1:
                                style.add('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey)
                                style.add('FONTSIZE', (0, 0), (-1, 0), 10)
                                style.add('ALIGN', (0, 0), (-1, 0), 'CENTER')
                                # 添加表头下方的粗分隔线
                                style.add('LINEBELOW', (0, 0), (-1, 0), 1.5, colors.black)
                            table.setStyle(style)
                            elements.append(table)
                            elements.append(Spacer(1, 0.1 * inch))
                        in_table = False
                
                i += 1
                continue
            
            # 如果之前在表格中，但当前行不是表格行，结束表格
            if in_table:
                # 创建并添加表格
                if table_rows:
                    table = Table(table_rows)
                    # 为表格添加样式
                    style = TableStyle([
                        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                        ('FONTNAME', (0, 0), (-1, -1), font_name),
                        ('FONTSIZE', (0, 0), (-1, -1), 9),
                        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                        ('TOPPADDING', (0, 0), (-1, -1), 3),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                    ])
                    # 如果有表头（第一行）设置特殊样式
                    if len(table_rows) > 1:
                        style.add('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey)
                        style.add('FONTSIZE', (0, 0), (-1, 0), 10)
                        style.add('ALIGN', (0, 0), (-1, 0), 'CENTER')
                        # 添加表头下方的粗分隔线
                        style.add('LINEBELOW', (0, 0), (-1, 0), 1.5, colors.black)
                    table.setStyle(style)
                    elements.append(table)
                    elements.append(Spacer(1, 0.1 * inch))
                in_table = False
                
            # 匹配集数
            if ('第' in line and '集' in line) or ('集数' in line):
                # 检查标题是否有##前缀
                is_markdown_title = line.strip().startswith('##')
                
                # 如果是Markdown标题格式，去掉前面的##
                if is_markdown_title:
                    line = line.strip()[2:].strip()
                    
                # 检查并处理带有**标记的集标题或(完)标记
                if '**' in line or '(完)' in line:
                    # 替换**标记为HTML粗体标签
                    clean_line = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', line)
                    
                    # 首先尝试匹配"集数：第X集"格式
                    episode_num_match = episode_number_format.search(clean_line)
                    if episode_num_match:
                        new_episode = episode_num_match.group(1)
                        # 添加分页符
                        elements.append(PageBreak())
                        # 记录新的当前集数
                        current_episode = new_episode
                        elements.append(Paragraph(clean_line, styles['ChineseTitle']))
                        elements.append(Spacer(1, 0.2 * inch))
                        
                        # 跳过(完)后面可能有的重复内容处理
                        if '(完)' in line:
                            # 寻找下一个集数或场次的起始位置
                            next_section_index = None
                            for j in range(i+1, len(lines)):
                                # 检查是否是集数标题（带或不带##前缀）
                                if ('第' in lines[j] and '集' in lines[j]):
                                    # 支持带##前缀的格式
                                    if lines[j].strip().startswith('##'):
                                        if '集数' in lines[j] or ('第' in lines[j] and '集' in lines[j]):
                                            next_section_index = j
                                            break
                                    else:
                                        next_section_index = j
                                        break
                                # 检查是否是场次标题
                                current_line = lines[j].strip()
                                if current_line.startswith('场次') or (current_line.startswith('###') and '场次' in current_line):
                                    next_section_index = j
                                    break
                        
                            # 如果找到了下一节，直接跳到那里
                            if next_section_index is not None:
                                i = next_section_index
                                continue
                        i += 1
                        continue
                    
                    # 然后尝试匹配普通的"第X集"格式
                    episode_match = episode_format.search(clean_line)
                    if episode_match:
                        # 获取当前集数
                        new_episode = episode_match.group(1)
                        
                        # 仅在含有"集数："的集数标题前添加分页符
                        if "集数：" in line or "集数:" in line:
                            elements.append(PageBreak())
                        
                        # 记录新的当前集数
                        current_episode = new_episode
                            
                        elements.append(Paragraph(clean_line, styles['ChineseTitle']))
                        elements.append(Spacer(1, 0.2 * inch))
                else:
                    # 处理不带**标记的集数行
                    # 首先尝试匹配"集数：第X集"格式
                    episode_num_match = episode_number_format.search(line)
                    if episode_num_match:
                        # 获取当前集数
                        new_episode = episode_num_match.group(1)
                        # 添加分页符
                        elements.append(PageBreak())
                        # 记录新的当前集数
                        current_episode = new_episode
                        elements.append(Paragraph(line, styles['ChineseTitle']))
                        elements.append(Spacer(1, 0.2 * inch))
                        i += 1
                        continue
                    
                    # 然后尝试匹配普通的"第X集"格式
                    episode_match = episode_format.search(line)
                    if episode_match:
                        # 获取当前集数
                        new_episode = episode_match.group(1)
                        
                        # 仅在含有"集数："的集数标题前添加分页符
                        if "集数：" in line or "集数:" in line:
                            elements.append(PageBreak())
                        
                        # 记录新的当前集数
                        current_episode = new_episode
                            
                        elements.append(Paragraph(line, styles['ChineseTitle']))
                        elements.append(Spacer(1, 0.2 * inch))
                i += 1
                continue
            
            # 处理场次行
            elif line.strip().startswith('场次') or (line.strip().startswith('###') and '场次' in line.strip()):
                # 检查之前是否处理过(完)标记
                is_after_completion = False
                for j in range(i-1, -1, -1):
                    if j >= 0 and '(完)' in lines[j]:
                        is_after_completion = True
                        break
                    # 如果遇到新的集数标记，中断向前查找
                    if j >= 0 and ('第' in lines[j] and '集' in lines[j]):
                        # 检查是否是带有##前缀的集数标题
                        if lines[j].strip().startswith('##'):
                            # 确认是集数标题而不是其他##标记
                            if '集数' in lines[j] or '第' in lines[j] and '集' in lines[j]:
                                break
                        else:
                            break
                
                # 如果是(完)后的内容且没有新集数标记，可能是重复内容，跳过
                if is_after_completion:
                    # 查找下一个集数标记
                    next_episode_index = None
                    for j in range(i+1, len(lines)):
                        if '第' in lines[j] and '集' in lines[j]:
                            next_episode_index = j
                            break
                    
                    if next_episode_index is not None:
                        i = next_episode_index
                        continue
                
                # 处理可能带有###前缀的场次行
                if line.strip().startswith('###'):
                    # 移除###前缀
                    line = line.strip()[3:].strip()
                
                # 检查是否有粗体标记
                if '**' in line:
                    # 替换星号为HTML粗体标签
                    line = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', line)
                    
                # 匹配场次，支持"场次X-Y："和"场次X-Y"格式
                scene_match = re.search(r'场次(\d+(?:-\d+)?)', line)
                if scene_match:
                    current_scene = scene_match.group(1)
                    
                    # 规范化场次编号 - 确保场次编号第一部分与当前集数一致
                    if current_episode is not None and '-' in current_scene:
                        scene_parts = current_scene.split('-')
                        if len(scene_parts) == 2 and int(scene_parts[0]) != current_episode:
                            # 修正场次编号的第一部分为当前集数
                            original_scene = current_scene
                            current_scene = f"{current_episode}-{scene_parts[1]}"
                            
                            # 修改显示的场次文本，替换原始场次编号为规范化的编号
                            line = line.replace(f"场次{original_scene}", f"场次{current_scene}")
                            print(f"规范化场次编号: 原编号={original_scene}, 新编号={current_scene} (属于第{current_episode}集)")
                    
                    # 记录场次
                    elements.append(Paragraph(line, styles['ChineseScene']))
                    print(f"识别到场次: {current_scene}")
                else:
                    # 如果完全无法提取场次，使用默认格式
                    elements.append(Paragraph(line, styles['ChineseScene']))
                    print(f"无法识别场次格式: {line}")
                i += 1
                continue
            
            # 处理Markdown标题（##和###）
            elif line.strip().startswith('##') and not line.strip().startswith('###'):
                # 处理二级标题
                clean_line = line.lstrip('#').strip()
                
                # 修改特定标题行的处理，不再跳过
                if clean_line in ["角色开发表", "短剧分集目录", "角色表", "目录", "角色介绍", "分集目录", "剧集目录"]:
                    print(f"保留二级标题: {clean_line}")
                    elements.append(Paragraph(clean_line, ParagraphStyle(
                        name='ChineseHeading2',
                        fontName=font_name,
                        fontSize=14,
                        leading=18,
                        spaceBefore=6,
                        spaceAfter=3,
                        textColor=colors.navy,
                        encoding='utf-8'
                    )))
                    i += 1
                    continue
                    
                # 处理粗体标记
                if '**' in clean_line:
                    clean_line = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', clean_line)
                
                elements.append(Paragraph(clean_line, ParagraphStyle(
                    name='ChineseHeading2',
                    fontName=font_name,
                    fontSize=14,
                    leading=18,
                    spaceBefore=6,
                    spaceAfter=3,
                    textColor=colors.navy,
                    encoding='utf-8'
                )))
                i += 1
                continue
            
            elif line.strip().startswith('###'):
                # 处理三级标题
                clean_line = line.lstrip('#').strip()
                
                # 修改特定标题行的处理，不再跳过
                if clean_line in ["角色开发表", "短剧分集目录", "角色表", "目录", "角色介绍", "分集目录", "剧集目录"]:
                    print(f"保留三级标题: {clean_line}")
                    elements.append(Paragraph(clean_line, ParagraphStyle(
                        name='ChineseHeading3',
                        fontName=font_name,
                        fontSize=12,
                        leading=16,
                        spaceBefore=4,
                        spaceAfter=2,
                        textColor=colors.darkblue,
                        encoding='utf-8'
                    )))
                    i += 1
                    continue
                    
                # 处理粗体标记
                if '**' in clean_line:
                    clean_line = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', clean_line)
                
                elements.append(Paragraph(clean_line, ParagraphStyle(
                    name='ChineseHeading3',
                    fontName=font_name,
                    fontSize=12,
                    leading=16,
                    spaceBefore=4,
                    spaceAfter=2,
                    textColor=colors.darkblue,
                    encoding='utf-8'
                )))
                i += 1
                continue
            
            # 处理提示词（画面描述词）并直接显示其对应图片
            elif line.startswith('#'):
                # 统一处理所有级别的markdown标题，支持 #、##、### 开头的提示词
                hash_count = 0
                for char in line:
                    if char == '#':
                        hash_count += 1
                    else:
                        break
                        
                # 检查是否是角色表或目录标题
                clean_line = line.lstrip('#').strip()
                if clean_line in ["角色开发表", "短剧分集目录", "角色表", "目录", "角色介绍", "分集目录", "剧集目录"]:
                    print(f"跳过特定标题: {clean_line}")
                    i += 1
                    continue
                
                # 检查是否在(完)标记之后
                is_after_completion = False
                for j in range(i-1, -1, -1):
                    if j >= 0 and '(完)' in lines[j]:
                        is_after_completion = True
                        break
                    # 如果遇到新的集数标记，中断向前查找
                    if j >= 0 and ('第' in lines[j] and '集' in lines[j]):
                        # 检查是否是带有##前缀的集数标题
                        if lines[j].strip().startswith('##'):
                            # 确认是集数标题而不是其他##标记
                            if '集数' in lines[j] or '第' in lines[j] and '集' in lines[j]:
                                break
                        else:
                            break
                
                # 跟踪已添加的图片，防止重复
                image_key = f"{current_episode}_{current_scene}_{line}"
                # 创建一个集合存储已处理的图片关键字，如果不存在则创建
                if not hasattr(generate_script_pdf, 'processed_images'):
                    generate_script_pdf.processed_images = set()
                
                # 检查当前图片是否已经处理过
                if image_key in generate_script_pdf.processed_images:
                    print(f"跳过已处理的图片: {image_key}")
                    i += 1
                    continue
                
                # 如果是(完)后的内容且没有新集数标记，可能是重复内容，跳过
                if is_after_completion:
                    i += 1
                    continue
                    
                # 添加提示词文本，移除所有前导#号
                clean_prompt = line.lstrip('#').strip()
                if clean_prompt:
 
                        
                    # 仅为一级提示词(#)添加图片，其他级别(##, ###)仅添加文本
                    if hash_count == 1 and current_episode is not None and current_scene is not None:
                        # 新的提示词索引计算方法：基于当前场次
                        # 获取当前场次的所有提示词行
                        scene_lines = []
                        scene_start_index = 0
                        current_scene_found = False
                        
                        # 先找到当前场次的开始位置
                        for j in range(i-1, -1, -1):
                            if j < len(lines):
                                current_line = lines[j].strip()
                                # 支持两种场次格式：普通"场次X-Y"和带前缀"### 场次X-Y"
                                is_scene_line = current_line.startswith('场次') or (current_line.startswith('###') and '场次' in current_line)
                                
                                if is_scene_line:
                                    # 提取场次编号，不管是否有###前缀
                                    scene_line = current_line
                                    if current_line.startswith('###'):
                                        scene_line = current_line[3:].strip()
                                        
                                    scene_match = re.search(r'场次(\d+(?:-\d+)?)', scene_line)
                                    if scene_match and scene_match.group(1) == current_scene:
                                        scene_start_index = j
                                        current_scene_found = True
                                        break
                                    elif scene_match:  # 找到了前一个场次
                                        break
                        
                        if current_scene_found:
                            # 计算从当前场次开始到当前行之间的提示词数量
                            prompt_count = 0
                            for j in range(scene_start_index, i+1):
                                if j < len(lines) and lines[j].strip().startswith('#') and not lines[j].strip().startswith('##') and not lines[j].strip().startswith('###'):
                                    prompt_count += 1
                                    if j == i:  # 这是当前提示词
                                        break
                            
                            # 当前提示词的索引 (从0开始)
                            prompt_idx = str(prompt_count - 1)  # 减1是因为我们要从0开始索引
                            print(f"场次 {current_scene} 的第 {prompt_idx} 个提示词")
                        else:
                            prompt_idx = "0"  # 默认为第一个提示词
                            print(f"无法确定场次 {current_scene} 的位置，使用默认提示词索引 0")
                        
                        # 尝试获取图片 - 改进的查找逻辑
                        image_paths = []
                        
                        # 首先检查是否启用了MinIO
                        from app.core.config import MINIO_ENABLED, SAVE_FILES_LOCALLY
                        from app.utils.minio_storage import minio_client, get_image_object_name
                        
                        # 创建一个集合以避免重复加载相同的图片
                        processed_image_paths = set()
                        images_found = False
                        
                        if image_dir and (os.path.exists(image_dir) or MINIO_ENABLED):
                            # 构建图片文件名格式
                            episode_formatted = f"第{current_episode}集"
                            
                            # 规范化场次编号 - 只对第二集特殊处理，如果遇到场次2-1，2-2等需要保留原样
                            scene_for_search = current_scene
                            if current_episode == "2" and current_scene.startswith("2-"):
                                # 对于2-开头的场次，如果当前是第2集，保留原始场次编号不变
                                scene_for_search = current_scene
                                print(f"第2集场次 {current_scene} - 保留原始场次编号不变")
                            elif '-' in current_scene:
                                scene_parts = current_scene.split('-')
                                if len(scene_parts) == 2 and int(scene_parts[0]) != int(current_episode):
                                    # 修正场次编号，确保第一部分与集数一致
                                    scene_for_search = f"{current_episode}-{scene_parts[1]}"
                                    print(f"查找图片时规范化场次编号为: {scene_for_search}")
                            elif current_scene.isdigit() and int(current_scene) != int(current_episode):
                                # 对于单数字场次，如果不等于当前集数，尝试使用"集数-场次"格式
                                scene_for_search = f"{current_episode}-{current_scene}"
                                print(f"为单数字场次创建复合编号: {scene_for_search}")
                            
                            scene_formatted = f"场次{scene_for_search}"
                            
                            # 创建场次格式列表，按优先级排序
                            scene_formats_to_try = []
                            
                            # 1. 首先尝试 "第X集_场次Y-Z" 格式
                            if '-' in scene_for_search:
                                scene_formats_to_try.append(scene_formatted)
                            
                            # 2. 尝试 "第X集_场次Z" 格式（如果有连字符，使用第二部分）
                            if '-' in scene_for_search:
                                scene_parts = scene_for_search.split('-')
                                if len(scene_parts) == 2:
                                    scene_formats_to_try.append(f"场次{scene_parts[1]}")
                            
                            # 3. 尝试原始场次格式
                            if scene_formatted not in scene_formats_to_try:
                                scene_formats_to_try.append(scene_formatted)
                            
                            # 4. 对于连字符场次，尝试反向格式
                            if '-' in scene_for_search:
                                scene_parts = scene_for_search.split('-')
                                if len(scene_parts) == 2:
                                    reverse_format = f"场次{scene_parts[1]}-{scene_parts[0]}"
                                    if reverse_format not in scene_formats_to_try:
                                        scene_formats_to_try.append(reverse_format)
                            
                            # 打印尝试的场次格式
                            print(f"将按优先级尝试以下场次格式查找图片: {', '.join(scene_formats_to_try)}")
                            
                            # 对每种场次格式尝试查找，按优先级排序
                            for scene_fmt in scene_formats_to_try:
                                # 如果已经找到足够的图片，不再继续查找
                                if len(image_paths) >= 4:
                                    break
                                    
                                # 尝试多种提示词索引，优先使用计算出的索引
                                # 为了提高匹配成功率，我们扩展了索引尝试范围
                                prompt_indices_to_try = []
                                
                                # 1. 首先尝试指定索引
                                prompt_indices_to_try.append(prompt_idx)
                                
                                # 2. 然后尝试索引0（常用于第一个提示词）
                                if prompt_idx != "0":
                                    prompt_indices_to_try.append("0")
                                    
                                # 3. 如果提示词索引较大，也尝试1和2（常见索引）
                                if int(prompt_idx) > 2:
                                    prompt_indices_to_try.append("1")
                                    prompt_indices_to_try.append("2")
                                
                                # 4. 如果当前索引是0，尝试相邻的索引
                                if prompt_idx == "0":
                                    prompt_indices_to_try.append("1")
                                    
                                # 对每种提示词索引尝试查找
                                for p_idx in prompt_indices_to_try:
                                    # 如果当前索引下已找到图片，跳过其他索引
                                    if images_found:
                                        break
                                        
                                    # 尝试查找最多4张图片
                                    temp_image_paths = []
                                    has_images_for_current_index = False
                                    
                                    for img_idx in range(4):  # 最多预期4张图片
                                        # 生成图片文件名
                                        img_filename = f"{episode_formatted}_{scene_fmt}_{p_idx}_img{img_idx}.png"
                                        local_img_path = os.path.join(image_dir, img_filename)
                                        
                                        # 第一步：检查本地文件系统（如果启用了本地保存）
                                        local_image_found = False
                                        if SAVE_FILES_LOCALLY and os.path.exists(local_img_path) and local_img_path not in processed_image_paths:
                                            # 压缩图片到临时目录
                                            compressed_img_path = os.path.join(
                                                temp_dir,
                                                f"compressed_{img_filename.replace('.png', '.jpg')}"
                                            )
                                            # 进行图片压缩
                                            compressed_img_path = compress_image(
                                                local_img_path, 
                                                compressed_img_path,
                                                quality=75,  # 设置合适的压缩质量
                                                max_size=(1200, 1200)  # 设置合适的最大尺寸
                                            )
                                            
                                            # 记录已处理的图片路径，避免重复
                                            processed_image_paths.add(local_img_path)
                                            temp_image_paths.append(compressed_img_path)
                                            has_images_for_current_index = True
                                            local_image_found = True
                                            print(f"找到并压缩本地图片: {local_img_path} -> {compressed_img_path}")
                                        
                                        # 第二步：如果本地没有找到且启用了MinIO，尝试从MinIO下载
                                        if not local_image_found and MINIO_ENABLED and minio_client.is_available():
                                            # 构建MinIO对象名
                                            object_name = get_image_object_name(task_id, img_filename)
                                            
                                            # 尝试从MinIO下载图片
                                            image_data = minio_client.download_bytes(object_name)
                                            
                                            if image_data:
                                                # 将图片数据写入临时文件
                                                temp_img_path = os.path.join(temp_dir, img_filename)
                                                with open(temp_img_path, 'wb') as f:
                                                    f.write(image_data)
                                                
                                                # 压缩图片
                                                compressed_img_path = os.path.join(
                                                    temp_dir,
                                                    f"compressed_{img_filename.replace('.png', '.jpg')}"
                                                )
                                                compressed_img_path = compress_image(
                                                    temp_img_path, 
                                                    compressed_img_path,
                                                    quality=75,
                                                    max_size=(1200, 1200)
                                                )
                                                
                                                temp_image_paths.append(compressed_img_path)
                                                has_images_for_current_index = True
                                                print(f"从MinIO下载并压缩图片: {object_name} -> {compressed_img_path}")
                                    
                                    # 如果在当前索引下找到了图片，添加到结果列表
                                    if has_images_for_current_index:
                                        image_paths.extend(temp_image_paths)
                                        images_found = True
                                        print(f"使用索引 {p_idx} 在格式 '{scene_fmt}' 下找到 {len(temp_image_paths)} 张图片")
                        
                        # 如果没有找到任何图片，尝试全局搜索
                        if not image_paths:
                            print(f"未找到任何精确匹配图片，尝试全局搜索")
                            
                            # 确保episode_formatted已定义
                            if 'episode_formatted' not in locals():
                                if current_episode:
                                    episode_formatted = f"第{current_episode}集"
                                else:
                                    episode_formatted = "未知集数"
                                print(f"创建默认集数格式: {episode_formatted}")
                            
                            # 查找任何包含当前集数的图片
                            episode_pattern = episode_formatted
                            
                            # 存储按场景和提示词索引分组的图片
                            grouped_images = {}
                            
                            # 首先从MinIO搜索图片
                            if MINIO_ENABLED and minio_client.is_available():
                                print(f"在MinIO中搜索包含 '{episode_pattern}' 的图片")
                                
                                # 列出所有图片对象
                                image_objects = minio_client.list_objects(f"{IMAGE_PREFIX}{task_id}/")
                                
                                for obj in image_objects:
                                    filename = os.path.basename(obj["name"])
                                    if episode_pattern in filename and filename.endswith('.png'):
                                        try:
                                            # 提取图片信息
                                            parts = filename.split('_')
                                            if len(parts) >= 4:
                                                img_scene = parts[1]  # 场次
                                                img_prompt_idx = parts[2]  # 提示词索引
                                                
                                                # 使用场景和提示词索引作为分组键
                                                group_key = f"{img_scene}_{img_prompt_idx}"
                                                
                                                if group_key not in grouped_images:
                                                    grouped_images[group_key] = []
                                                
                                                # 下载并压缩图片
                                                image_data = minio_client.download_bytes(obj["name"])
                                                if image_data:
                                                    # 将图片数据写入临时文件
                                                    temp_img_path = os.path.join(temp_dir, filename)
                                                    with open(temp_img_path, 'wb') as f:
                                                        f.write(image_data)
                                                    
                                                    # 压缩图片
                                                    compressed_img_path = os.path.join(
                                                        temp_dir,
                                                        f"compressed_{filename.replace('.png', '.jpg')}"
                                                    )
                                                    compressed_img_path = compress_image(
                                                        temp_img_path, 
                                                        compressed_img_path,
                                                        quality=75,
                                                        max_size=(1200, 1200)
                                                    )
                                                    
                                                    grouped_images[group_key].append(compressed_img_path)
                                                    print(f"从MinIO下载并压缩图片组: {group_key} - {filename}")
                                        except Exception as e:
                                            print(f"处理MinIO图片时出错: {filename}, 错误: {str(e)}")
                            
                            # 然后从本地文件系统搜索（如果启用了本地存储）
                            if SAVE_FILES_LOCALLY and os.path.exists(image_dir):
                                # 扫描目录
                                for filename in os.listdir(image_dir):
                                    if episode_pattern in filename and filename.endswith('.png'):
                                        try:
                                            # 提取图片信息
                                            parts = filename.split('_')
                                            if len(parts) >= 4:
                                                img_scene = parts[1]  # 场次
                                                img_prompt_idx = parts[2]  # 提示词索引
                                                
                                                # 使用场景和提示词索引作为分组键
                                                group_key = f"{img_scene}_{img_prompt_idx}"
                                                
                                                if group_key not in grouped_images:
                                                    grouped_images[group_key] = []
                                                    
                                                # 压缩图片到临时目录
                                                original_img_path = os.path.join(image_dir, filename)
                                                compressed_img_path = os.path.join(
                                                    temp_dir,
                                                    f"compressed_{filename.replace('.png', '.jpg')}"
                                                )
                                                # 进行图片压缩
                                                compressed_img_path = compress_image(
                                                    original_img_path, 
                                                    compressed_img_path,
                                                    quality=75,
                                                    max_size=(1200, 1200)
                                                )
                                                
                                                grouped_images[group_key].append(compressed_img_path)
                                                print(f"找到本地图片组: {group_key} - {filename}")
                                        except Exception as e:
                                            print(f"解析文件名时出错: {filename}, 错误: {str(e)}")
                            
                            # 如果找到了分组，按优先级选择
                            if grouped_images:
                                # 首先尝试查找当前场次的图片
                                current_scene_keys = [k for k in grouped_images.keys() if k.startswith(f"场次{current_scene}_")]
                                if current_scene_keys:
                                    # 使用当前场次的第一组图片
                                    group_key = current_scene_keys[0]
                                    image_paths = sorted(grouped_images[group_key])[:4]
                                    print(f"找到当前场次的图片组: {group_key}")
                                else:
                                    # 使用任何可用的图片组，优先选择索引0的组
                                    index0_keys = [k for k in grouped_images.keys() if k.endswith("_0")]
                                    if index0_keys:
                                        group_key = index0_keys[0]
                                    else:
                                        # 使用第一个可用的组
                                        group_key = list(grouped_images.keys())[0]
                                        
                                    image_paths = sorted(grouped_images[group_key])[:4]
                                    print(f"使用替代图片组: {group_key}")
                                    
                                # 限制最多4张图片
                                image_paths = image_paths[:4]
                        
                        # 如果成功找到图片，创建一个图片表格，直接放在对应的提示词下方
                        if image_paths:
                            # 记录当前已处理的图片，防止重复添加
                            generate_script_pdf.processed_images.add(image_key)
                            
                            # 为2x2表格调整图片尺寸
                            max_width = 2.5 * inch  # 为2x2表格调整合适的图片宽度
                            max_height = 2.0 * inch  # 为2x2表格调整合适的图片高度
                            img_objs = []
                            
                            for img_path in image_paths:
                                try:
                                    # 使用PIL打开图片获取原始尺寸
                                    pil_img = PILImage.open(img_path)
                                    img_w, img_h = pil_img.size
                                    
                                    # 计算宽高比
                                    aspect_ratio = img_h / img_w
                                    
                                    # 确定是按宽度还是高度缩放
                                    if aspect_ratio * max_width <= max_height:
                                        # 宽度优先 - 图片较宽
                                        width = max_width
                                        height = width * aspect_ratio
                                    else:
                                        # 高度优先 - 图片较高
                                        height = max_height
                                        width = height / aspect_ratio
                                    
                                    # 按计算出的宽高创建图片
                                    img = Image(img_path, width=width, height=height)
                                    
                                    # 打印调试信息
                                    print(f"图片尺寸: 原始:{img_w}x{img_h}, 比例:{aspect_ratio:.2f}, PDF中:{width:.1f}x{height:.1f}")
                                    
                                    img_objs.append(img)
                                except Exception as e:
                                    print(f"处理图片失败: {str(e)}")
                            
                            # 最多取前4张图片
                            img_objs = img_objs[:4]
                            
                            # 创建2x2表格布局
                            if img_objs:
                                # 如果图片数量少于4，用空白补足
                                while len(img_objs) < 4:
                                    # 添加空白占位
                                    img_objs.append(Spacer(1, 0.1 * inch))
                                
                                # 创建2x2表格
                                img_table_data = [
                                    [img_objs[0], img_objs[1]],
                                    [img_objs[2], img_objs[3]]
                                ]
                                
                                # 创建表格并设置样式
                                img_table = Table(img_table_data)
                                img_table.setStyle(TableStyle([
                                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                                    ('LEFTPADDING', (0, 0), (-1, -1), 5),
                                    ('RIGHTPADDING', (0, 0), (-1, -1), 5),
                                    ('TOPPADDING', (0, 0), (-1, -1), 5),
                                    ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                                ]))
                                
                                elements.append(img_table)
                                elements.append(Spacer(1, 0.2 * inch))  # 表格后添加间隔
                        else:
                            # 未找到图片时添加明显标记
                            elements.append(Paragraph(f"【警告：未找到匹配图片】", ParagraphStyle(
                                name='WarningStyle',
                                fontName=font_name,
                                fontSize=12,
                                leading=14,
                                textColor=colors.red,
                                encoding='utf-8'
                            )))
                            elements.append(Paragraph(f"提示词索引: {prompt_idx}, 场次: {current_scene}, 集数: {current_episode}", ParagraphStyle(
                                name='WarningDetailStyle',
                                fontName=font_name,
                                fontSize=10,
                                leading=12,
                                textColor=colors.grey,
                                encoding='utf-8'
                            )))
                            
                            # 添加详细信息，帮助用户排查问题
                            if image_dir and os.path.exists(image_dir):
                                elements.append(Paragraph(f"预期图片路径格式: 第{current_episode}集_场次{current_scene}_{prompt_idx}_img*.png", ParagraphStyle(
                                    name='WarningExplanation',
                                    fontName=font_name,
                                    fontSize=8,
                                    leading=12,
                                    textColor=colors.grey,
                                    encoding='utf-8'
                                )))
                                
                                # 检查当前场景是否有其他图片
                                scene_pattern = f"第{current_episode}集_场次{current_scene}_"
                                other_images = []
                                for filename in os.listdir(image_dir):
                                    if filename.startswith(scene_pattern) and filename.endswith(".png"):
                                        other_images.append(filename)
                                
                                if other_images:
                                    elements.append(Paragraph(f"当前场景的可用图片: {', '.join(other_images[:5])}{' ...' if len(other_images) > 5 else ''}", ParagraphStyle(
                                        name='AvailableImages',
                                        fontName=font_name,
                                        fontSize=8,
                                        leading=12,
                                        textColor=colors.grey,
                                        encoding='utf-8'
                                    )))
                            
                            # 添加空间
                            elements.append(Spacer(1, 0.5 * inch))
                i += 1
                continue
            
            # 正常文本内容
            elif not line.startswith('#'):  # 排除提示词
                # 检查是否是角色表或目录标题
                if line.strip() in ["角色开发表", "短剧分集目录", "角色表", "目录", "角色介绍", "分集目录", "剧集目录"]:
                    # 修改为保留目录标题，不再跳过
                    print(f"保留目录标题文本: {line.strip()}")
                    elements.append(Paragraph(line.strip(), styles['ChineseHeading2']))
                    i += 1
                    continue
                    
                # 完全跳过任何位置包含"剧名"的行，只允许前20行显示
                if i > 20 and ("剧名" in line or "剧名:" in line or "剧名：" in line or "《" in line and "》" in line):
                    print(f"强制跳过目录后的剧名行: {line.strip()} (行号:{i})")
                    i += 1
                    continue
                
                # 跳过剧名行以及目录后的各种剧名相关内容
                if i > 20:  # 只检查目录后的内容
                    # 统一处理所有形式的剧名行，使用更宽松的匹配条件
                    if (line.strip().startswith("剧名：") or 
                        line.strip().startswith("剧名:") or 
                        line.strip().startswith("# 剧名") or  # 处理特殊形式"# 剧名："
                        ("剧名：" in line) or 
                        ("剧名:" in line) or
                        ("剧名" in line and ("《" in line or "》" in line)) or
                        # 添加更宽松的判断，针对可能有特殊格式的"剧名"行
                        ("剧名" in line.lower().replace(" ", ""))):
                        print(f"跳过目录后的剧名行: {line.strip()} (行号:{i})")
                        i += 1
                        continue
                else:
                    # 如果是文档顶部的剧名行，保留显示
                    if (line.strip().startswith("剧名：") or 
                        line.strip().startswith("剧名:") or 
                        line.strip().startswith("# 剧名") or  # 新增：匹配文档顶部的"# 剧名"格式
                        "剧名" in line.strip()):
                        print(f"保留顶部剧名行: {line.strip()} (行号:{i})")
                        
                # 下面的判断逻辑已被上面的逻辑代替，删除原来的冗余代码
                # 删除以下部分：
                # 明确跳过包含"剧名："但不在开头的行
                # if "剧名：" in line or "剧名:" in line:
                #    if i > 20:  # 同样只跳过目录后的
                #        print(f"跳过包含剧名的行: {line.strip()}")
                #        i += 1
                #        continue
                #        
                # 跳过其他类似格式的行，如"剧名"
                # if "剧名" in line and "《" in line and "》" in line:
                #    if i > 20:  # 同样只跳过目录后的
                #        print(f"跳过包含剧名和书名号的行: {line.strip()}")
                #        i += 1
                #        continue
                
                # 检查是否包含Markdown标记，首先检查粗体
                has_bold = '**' in line or '__' in line
                has_markdown = has_bold or any(md_char in line for md_char in ['*', '_', '`', '[', '##', '-', '1.', '>'])
                
                # 解析Markdown格式
                if has_markdown:
                    # 检查特殊格式
                    if line.startswith('##'):  # 二级标题
                        clean_line = line.replace('##', '').strip()
                        # 处理可能存在的粗体标记
                        if '**' in clean_line:
                            clean_line = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', clean_line)
                        elements.append(Paragraph(clean_line, styles['ChineseHeading']))
                    elif line.startswith('#') and not line.startswith('##'):  # 一级标题
                        clean_line = line.replace('#', '').strip()
                        # 处理可能存在的粗体标记
                        if '**' in clean_line:
                            clean_line = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', clean_line)
                        
                        # 检查是否是需要跳过的标题
                        if clean_line in ["角色开发表", "短剧分集目录", "角色表", "目录", "角色介绍", "分集目录", "剧集目录"]:
                            print(f"跳过普通文本处理中的特定标题: {clean_line}")
                            i += 1
                            continue
                        
                        elements.append(Paragraph(clean_line, styles['ChineseTitle']))
                    elif line.strip().startswith('- ') or line.strip().startswith('* '):  # 无序列表
                        # 处理列表项
                        clean_line = line.strip()[2:].strip()
                        # 处理可能存在的粗体标记
                        if '**' in clean_line:
                            clean_line = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', clean_line)
                        elements.append(Paragraph(f"• {clean_line}", styles['ChineseListItem']))
                    elif re.match(r'^\d+\.\s', line.strip()):  # 有序列表
                        # 提取编号和内容
                        match = re.match(r'^(\d+)\.(\s.*)', line.strip())
                        if match:
                            num, content = match.groups()
                            content = content.strip()
                            # 处理可能存在的粗体标记
                            if '**' in content:
                                content = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', content)
                            elements.append(Paragraph(f"{num}. {content}", styles['ChineseListItem']))
                    elif line.startswith('> '):  # 引用
                        # 处理引用
                        clean_line = line[2:].strip()
                        # 处理可能存在的粗体标记
                        if '**' in clean_line:
                            clean_line = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', clean_line)
                        elements.append(Paragraph(clean_line, ParagraphStyle(
                            name='ChineseQuote',
                            fontName=font_name,
                            fontSize=10,
                            leading=14,
                            leftIndent=20,
                            textColor=colors.darkgrey,
                            encoding='utf-8'
                        )))
                    elif '[' in line and '](' in line:  # 处理链接
                        # 将Markdown链接转换为HTML链接
                        clean_line = re.sub(r'\[(.*?)\]\((.*?)\)', r'<a href="\2">\1</a>', line)
                        # 处理可能存在的粗体标记
                        if '**' in clean_line:
                            clean_line = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', clean_line)
                        elements.append(Paragraph(clean_line, styles['ChineseLink']))
                    elif has_bold:  # 粗体 - 放在前面以确保优先处理
                        # 替换Markdown粗体标记
                        clean_line = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', line)
                        clean_line = re.sub(r'__(.*?)__', r'<b>\1</b>', clean_line)
                        # 检查是否还有其他格式需要处理
                        if '*' in clean_line and not '**' in clean_line:  # 斜体
                            clean_line = re.sub(r'\*(.*?)\*', r'<i>\1</i>', clean_line)
                        if '_' in clean_line and not '__' in clean_line:  # 斜体
                            clean_line = re.sub(r'_(.*?)_', r'<i>\1</i>', clean_line)
                        if '`' in clean_line:  # 代码
                            clean_line = re.sub(r'`(.*?)`', r'<code>\1</code>', clean_line)
                        elements.append(Paragraph(clean_line, styles['ChineseNormal']))
                    elif '*' in line or '_' in line:  # 斜体
                        # 替换Markdown斜体标记
                        clean_line = re.sub(r'\*(.*?)\*', r'<i>\1</i>', line)
                        clean_line = re.sub(r'_(.*?)_', r'<i>\1</i>', clean_line)
                        elements.append(Paragraph(clean_line, styles['ChineseNormal']))
                    elif '`' in line:  # 代码
                        # 处理行内代码
                        clean_line = re.sub(r'`(.*?)`', r'<code>\1</code>', line)
                        elements.append(Paragraph(clean_line, styles['ChineseNormal']))
                    else:
                        # 其他Markdown格式，使用解析器处理
                        html_content = parse_markdown(line)
                        elements.append(Paragraph(html_content, styles['ChineseNormal']))
                else:
                    # 其他未分类的文本
                    # 处理可能存在的粗体标记
                    if '**' in line:
                        line = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', line)
                    
                    elements.append(Paragraph(line, styles['ChineseNormal']))
                i += 1
                continue
            else:
                # 其他未分类的文本
                # 处理可能存在的粗体标记
                if '**' in line:
                    line = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', line)
                
                elements.append(Paragraph(line, styles['ChineseNormal']))
                i += 1
        
        # 更新进度
        if progress_callback:
            progress_callback(90, "生成PDF文档...")
        
        # 构建PDF
        doc.build(elements)
        
        # 更新进度
        if progress_callback:
            progress_callback(100, "PDF生成完成")
        
        # 如果没有指定输出路径，返回PDF字节数据
        if output_path is None:
            buffer.seek(0)
            return buffer.getvalue()
        else:
            return None
    finally:
        # 清理临时目录
        shutil.rmtree(temp_dir)


async def create_script_pdf(
    task_id: str, 
    script_content: str, 
    image_data: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]], 
    output_dir: str = None,
    filename: str = None,
    with_progress: bool = False
) -> str:
    """
    创建剧本PDF文件
    
    Args:
        task_id: 任务ID
        script_content: 剧本内容
        image_data: 图片数据
        output_dir: 输出目录
        filename: 输出文件名
        with_progress: 是否启用进度回调
        
    Returns:
        str: PDF文件路径或URL
    """
    from app.core.config import PDFS_DIR, MINIO_ENABLED, SAVE_FILES_LOCALLY
    from app.utils.minio_storage import minio_client, get_pdf_object_name
    
    # 设置默认输出目录
    if output_dir is None:
        output_dir = PDFS_DIR
    
    # 确保目录存在
    os.makedirs(output_dir, exist_ok=True)
    
    # 设置默认文件名
    if filename is None:
        # timestamp = int(time.time())
        filename = f"{task_id}.pdf"
    
    # 完整文件路径
    full_path = os.path.join(output_dir, filename)
    
    # 创建PDF文档
    if with_progress:
        # 定义更新进度的回调函数
        def update_progress(progress: int, message: str):
            # 此处可添加进度更新逻辑
            print(f"PDF生成进度: {progress}% - {message}")
    else:
        update_progress = None
    
    # 初始化pdf_data为None，避免未赋值就引用
    pdf_data = None
    minio_upload_success = False
    
    try:
        # 尝试生成PDF文档
        pdf_data = await generate_script_pdf(
            script_content=script_content, 
            image_data=image_data, 
            output_path=full_path if SAVE_FILES_LOCALLY else None,
            task_id=task_id,
            progress_callback=update_progress
        )
    except Exception as e:
        print(f"生成PDF时发生错误: {str(e)}")
        import traceback
        print(traceback.format_exc())
        
    # 如果没有保存到本地但生成了PDF数据，先不保存，等待MinIO上传结果
    local_saved = SAVE_FILES_LOCALLY and os.path.exists(full_path)
    
    # 如果启用了MinIO，尝试上传到MinIO并获取URL
    minio_url = None
    if MINIO_ENABLED and minio_client.is_available() and (local_saved or pdf_data):
        try:
            # 定义在MinIO中的对象名
            object_name = get_pdf_object_name(task_id, filename)
            
            # 将PDF上传到MinIO
            if local_saved:
                # 上传本地文件
                success, url = minio_client.upload_file(full_path, object_name, 'application/pdf')
            elif pdf_data:
                # 直接上传PDF数据
                success, url = minio_client.upload_bytes(pdf_data, object_name, 'application/pdf')
            else:
                success, url = False, None
            
            if success:
                print(f"PDF已上传到MinIO: {object_name}")
                minio_upload_success = True
                minio_url = url
            else:
                print(f"上传PDF到MinIO失败: {url}")
        except Exception as e:
            print(f"上传PDF到MinIO过程中出错: {str(e)}")
            
    # MinIO上传失败且未保存到本地，但有PDF数据，确保保存到本地
    if not minio_upload_success and not local_saved and pdf_data:
        try:
            with open(full_path, 'wb') as f:
                f.write(pdf_data)
            print(f"MinIO上传失败，PDF数据已保存到本地文件: {full_path}")
            local_saved = True
        except Exception as e:
            print(f"保存PDF数据到本地备份文件失败: {str(e)}")
    
    # 如果MinIO上传成功，返回MinIO URL，否则返回本地路径
    return minio_url if minio_upload_success else full_path

# 添加一个新函数，用于从generation_states文件夹读取剧本并生成PDF
async def generate_pdf_from_script_file(
    script_file_path: str,
    output_dir: str = None,
    filename: str = None,
    with_progress: bool = False
) -> str:
    """
    从脚本文件生成PDF
    
    Args:
        script_file_path: 脚本文件路径(.pkl文件)
        output_dir: 输出目录
        filename: 输出文件名
        with_progress: 是否启用进度回调
        
    Returns:
        str: PDF文件路径或URL
    """
    try:
        # 加载脚本内容
        script_content, _ = load_script_from_pkl(script_file_path)
        
        # 从文件路径中提取任务ID
        task_id = os.path.basename(script_file_path).split('.')[0]
        
        # 创建空图片数据，暂不包含图片
        image_data = {}
        
        # 生成PDF
        pdf_path = await create_script_pdf(
            task_id=task_id,
            script_content=script_content,
            image_data=image_data,
            output_dir=output_dir,
            filename=filename,
            with_progress=with_progress
        )
        
        return pdf_path
    except Exception as e:
        print(f"生成PDF失败: {str(e)}")
        raise

# 添加图片压缩函数
def compress_image(input_path, output_path, quality=70, max_size=(1024, 1024)):
    """
    压缩图片并保存为jpg格式
    
    Args:
        input_path: 输入图片路径
        output_path: 输出图片路径
        quality: jpg质量（1-100）
        max_size: 最大尺寸
        
    Returns:
        str: 输出图片路径
    """
    try:
        # 打开原始图片
        with PILImage.open(input_path) as img:
            # 转换为RGB模式（确保可以保存为jpg）
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # 调整图片大小，保持比例
            if img.width > max_size[0] or img.height > max_size[1]:
                img.thumbnail(max_size, PILImage.Resampling.LANCZOS)
            
            # 保存为jpg
            img.save(output_path, 'JPEG', quality=quality, optimize=True)
            
            # 打印压缩信息
            original_size = os.path.getsize(input_path)
            compressed_size = os.path.getsize(output_path)
            ratio = (1 - compressed_size / original_size) * 100
            print(f"图片压缩: {input_path} ({original_size/1024:.1f}KB) -> {output_path} ({compressed_size/1024:.1f}KB), 减少了 {ratio:.1f}%")
            
            return output_path
    except Exception as e:
        print(f"压缩图片失败: {str(e)}")
        # 压缩失败时返回原始路径
        return input_path