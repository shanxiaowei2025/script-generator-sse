# 剧本生成器 SSE API

基于Claude-3-7-Sonnet的AI剧本生成服务，采用Server-Sent Events (SSE)流式响应技术，可以生成完整的短剧剧本，包括角色表、分集目录和详细剧情。支持实时提取画面描述词。

## 特性

- 基于SSE的流式响应，实时传输生成内容
- 支持多任务并行生成和管理
- 可取消正在进行的生成任务
- 支持实时提取剧本中的画面描述词
- 高质量的剧本生成效果
- 简洁直观的用户界面

## 项目结构

```
app/
├── api/             # API路由和控制器
├── core/            # 核心功能和配置
├── models/          # 数据模型和Schema
├── storage/         # 存储目录
│   ├── generation_states/  # 生成状态存储
│   └── partial_contents/   # 部分内容存储
└── utils/           # 工具函数
```

## 安装和运行

1. 克隆仓库

```bash
git clone <repository-url>
cd <repository-directory>
```

2. 安装依赖

```bash
pip install -r requirements.txt
```

3. 设置环境变量

创建`.env`文件或设置以下环境变量:

```
API_KEY=your_anthropic_api_key
API_URL=https://api.anthropic.com/v1/messages
APP_HOST=0.0.0.0
APP_PORT=8000
DEBUG=False
```

4. 运行服务

```bash
uvicorn app.main:app --host=0.0.0.0 --port=8000 --reload

# 或
python -m app.main
```

服务将在 `http://localhost:8000` 启动。

## API端点

### 流式生成剧本

```
POST /api/stream/generate-script
```

请求示例:

```json
{
  "genre": "都市职场",
  "duration": "3分钟",
  "episodes": 8,
  "characters": ["张明,男,28", "李婷,女,25", "王总,男,45"]
}
```

响应：返回SSE格式的事件流，包括task_id、进度更新和内容片段。

### 取消正在生成的剧本

```
DELETE /api/stream/cancel/{task_id}
```

### 提取画面描述词

```
POST /api/stream/extract-scene-prompts/{task_id}
```

请求参数:
- `task_id`: 任务ID，路径参数
- `episode`: 可选，请求体参数，指定要提取的集数

请求示例:

```json
{
  "task_id": "21f8d32a-1c3e-4e69-8123-afed96e7a321",
  "episode": 1
}
```

响应：返回SSE格式的事件流，包含提取的画面描述词。

## 前端使用

访问 `http://localhost:8000` 使用内置的Web界面。

前端JavaScript示例:

```javascript
// 发起剧本生成请求
fetch('/api/stream/generate-script', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(requestData)
})
.then(response => {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    
    function processStream({done, value}) {
        if (done) return;
        
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split('\n\n');
        buffer = events.pop() || '';
        
        for (const event of events) {
            // 处理SSE事件
            // ...
        }
        
        reader.read().then(processStream);
    }
    
    reader.read().then(processStream);
});

// 取消生成
fetch(`/api/stream/cancel/${taskId}`, { method: 'DELETE' });

// 提取画面描述词
fetch(`/api/stream/extract-scene-prompts/${taskId}`);
```

## 许可

MIT License 