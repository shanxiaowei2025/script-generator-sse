# 剧本生成器 API

基于Claude-3-7-Sonnet的AI剧本生成API服务。可以生成完整的短剧剧本，包括角色表、分集目录和详细剧情。

## 特性

- RESTful API 设计，便于前端集成
- 支持从断点恢复生成
- 支持后台任务生成
- 完整的状态管理和错误处理
- 可自定义的API密钥和URL
- 高质量的剧本生成

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
APP_PORT=8003
DEBUG=False
```

4. 运行服务

```bash
uvicorn app.main:app --host=0.0.0.0 --port=8003 --reload

# 或
python -m app.main
```

服务将在 `http://localhost:8003` 启动，API文档在 `http://localhost:8003/docs`。

## API端点

### 生成角色表和目录

```
POST /api/generate-initial
```

请求示例:

```json
{
  "genre": "都市职场",
  "duration": "3分钟",
  "episodes": 8,
  "characters": ["张明,男,28", "李婷,女,25", "王总,男,45"],
  "client_id": "unique_client_id"
}
```

### 生成单集剧本

```
POST /api/generate-episode
```

请求示例:

```json
{
  "genre": "都市职场",
  "duration": "3分钟",
  "episodes": 8,
  "episode": 1,
  "characters": ["张明,男,28", "李婷,女,25", "王总,男,45"],
  "client_id": "unique_client_id"
}
```

### 查询生成状态

```
POST /api/check-status
```

请求示例:

```json
{
  "client_id": "unique_client_id"
}
```

### 获取完整剧本

```
POST /api/full-script
```

请求示例:

```json
{
  "client_id": "unique_client_id"
}
```

### 后台生成完整剧本

```
POST /api/background-generate
```

请求示例:

```json
{
  "genre": "都市职场",
  "duration": "3分钟",
  "episodes": 8,
  "characters": ["张明,男,28", "李婷,女,25", "王总,男,45"],
  "client_id": "unique_client_id"
}
```

### 查询后台任务状态

```
GET /api/task-status/{task_id}
```

## 许可

[此处添加许可信息] 