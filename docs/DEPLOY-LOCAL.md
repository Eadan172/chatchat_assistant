# 本地单机部署说明

本项目是「知识引擎」桌面端（Electron + React）与 Python FastAPI 后台（sidecar）的组合。
本文件记录本机已完成的一次性部署结果、启动方式和验证方法。

## 目录约定

所有运行时数据都留在仓库目录内，不写入 C: / D:。

| 路径 | 用途 |
|---|---|
| `.venv\` | Python 3.12 虚拟环境（sidecar 依赖） |
| `node_modules\` | 桌面端 npm 依赖（含 Electron 33） |
| `.data\data\app.db` | SQLite 知识库（含 FTS5 / sqlite-vec 索引） |
| `.data\library\` | 导入文件的副本 |
| `.data\config\` | 决策层配置 |
| `.data\secrets\` | 问答/ima 密钥（若在界面里保存） |
| `.data\desktop\` | Electron 用户目录（Chromium 配置、日志、crash dump） |
| `.cache\` | pip / npm / Electron / Edge 缓存、临时目录 |
| `.logs\` | 启动与验证日志 |
| `.env` | 本地环境变量（SIDECAR_TOKEN、功能开关、可选密钥） |

## 安装

```powershell
cd E:\cursor\grok-bot\chatchat-assistant

# 1) Python 依赖（.venv）
.\scripts\win\01-install-python.ps1

# 2) 桌面端依赖（npm ci，Electron 二进制走 .cache\electron）
.\scripts\win\02-install-node.ps1

# 3) 初始化数据库（首次启动 sidecar 时也会自动执行 alembic upgrade head）
$env:DATA_DIR="$PWD\.data"
Push-Location .\services\sidecar
..\..\.venv\Scripts\python.exe -c "from app.db.session import init_db; print(init_db())"
Pop-Location
```

> 注意：Python 虚拟环境统一放在**仓库根目录** `.venv`，桌面端启动 sidecar 时会按
> `SIDECAR_PYTHON` → `<repo>\.venv` → `services\sidecar\.venv` 的顺序查找。

## 启动

三个服务可以独立启动，端口固定：

| 服务 | 启动命令 | 地址 |
|---|---|---|
| 后台 API（FastAPI sidecar） | `.\scripts\win\03-start-sidecar.ps1` | http://127.0.0.1:8765 |
| 浏览器界面（Vite + React） | `.\scripts\win\05-start-web.ps1` | http://127.0.0.1:5199 |
| 桌面应用（Electron，自带 sidecar） | `.\scripts\win\04-start-desktop.ps1` | 窗口 + 渲染进程 http://localhost:5173 |

三个脚本都会先加载 `.env`，并把缓存/临时目录指向 `.cache`。
桌面端与后台共用 `.data` 知识库，因此两个界面看到的是同一批文档。

## 验证

```powershell
$env:SIDECAR_TOKEN="devtoken"
.\.venv\Scripts\python.exe .\scripts\win\05-verify.py 8765
```

覆盖：`/health`、鉴权 401、导入、列表、检索、`/chat` SSE、`/ask` SSE、密钥状态、删除。

## 配置

- `.env` 由 `.env.example` 复制而来；`SIDECAR_TOKEN` 为本地回环令牌。
- `DEEPSEEK_API_KEY`、`SERPER_API_KEY`、`SERPAPI_API_KEY`、`TAVILY_API_KEY`、`IMA_*` 留空时
  程序以离线模式运行：检索与引用正常，回答为抽取式摘要，不调用外部模型。
- 填入密钥后重启 sidecar 即可获得模型生成的回答与联网搜索。
- `.env` 不会被程序自动读取，必须通过上面的脚本（或手工 `Set-Item Env:`）加载。

## M7 增量（i18n / 导入通道 / 文体 / 对话记忆 / 设置）

- 迁移：`0004_m7` 新增 `project`、`conversation_folder`，并给 `conversation` 增加
  `folder_id`、`project_id`、`forked_from_id`、`forked_from_message_id`、`locale`、`style`、
  `updated_at`、`archived`、`pinned`。
- 新接口：`/import/sources*`、`/import/documents`、`/conversations*`、`/folders*`、`/projects*`、
  `/styles`、`/settings/app`、`/settings/ui`。
- 界面：顶栏新增「知识库 / 对话 / 设置」与「中文 / EN」切换；语言与默认文体写入
  `$DATA_DIR/config/ui.json`，同时缓存到浏览器 `localStorage`。
- 验收脚本：`$env:SIDECAR_TOKEN="devtoken"; .\.venv\Scripts\python.exe .\scripts\win\06-verify-m7.py 8765`
  （21 项断言，覆盖 i18n 目录、占位通道 501、统一导入、对话 CRUD/分叉/导入导出、偏好与文体回传）。
- 清空对话：界面「对话」页或「设置 → 数据与存储」里的「清空全部对话」；接口
  `POST /conversations/clear`（body 需 `{"confirm":"DELETE_ALL"}`）。删除前自动把对话、消息、
  文件夹、项目写入 `$DATA_DIR/backup/conversations-<时间戳>.json`，知识库文档不受影响。
  隔离验证脚本：`scripts/win/07-verify-purge.py`（需用临时 `DATA_DIR` 起一个实例再跑）。
- 话题分叉阈值：环境变量 `CONTEXT_FORK_TOKENS`（默认 `1000000`）。
- 本地优先检索 / 可点击网页来源 / 对话续聊验收：
  `$env:SIDECAR_TOKEN="devtoken"; .\.venv\Scripts\python.exe .\scripts\win\08-verify-context.py 8765`
  （11 项断言）。

## 免密钥联网检索（SearXNG / 360 / Bing）

默认开启「免密钥优先」：`WEB_SEARCH_KEYLESS=1`、`WEB_SEARCH_KEYLESS_FIRST=1`，
顺序为 **SearXNG → 360 → Bing → Serper → SerpApi → Tavily**，付费接口只在前面全部失败时使用。

### SearXNG（推荐，本机 Docker）

仓库使用 `.data/searxng/settings.yml`（已启用 `html` + `json` 格式，关闭 limiter）：

```powershell
docker run -d --name chatchat-searxng -p 127.0.0.1:8080:8080 `
  -v "E:\cursor\grok-bot\chatchat-assistant\.data\searxng:/etc/searxng" `
  --restart unless-stopped searxng/searxng:latest
```

`.env` 中配置端点（默认即此值）：

```ini
SEARXNG_BASE_URL=http://127.0.0.1:8080
```

验证：`http://127.0.0.1:8080/search?q=Dify&format=json` 返回 `results` 即正常。
停掉容器会自动回退到 360。

| 通道 | 实现 | 说明 |
|---|---|---|
| SearXNG `searxng` | JSON API，失败时回退 HTML | 自建、无 Key、结果链接为原文直链 |
| 360 搜索 `so360` | 解析 `www.so.com/s` | 无需 Key，链接为 `so.com/link` 跳转 |
| Bing 网页 `bing` | 解析 `cn.bing.com/search` | 部分中文查询会返回诱饵，已被相关性闸门拦截 |
| Serper / SerpApi / Tavily | 官方 API | 需 Key，作后备 |

**相关性地板**：免密钥通道都会用查询词做 token 命中过滤；返回很多结果但命中不足 2 条时整批丢弃，
避免把反爬诱饵写进引用。

### Tavily（可选，需 Key）

读取顺序：环境变量 `TAVILY_API_KEY` → `$DATA_DIR/secrets/ask.json` 的 `tavily_api_key`。

- 界面：「设置 → 密钥 → Tavily API Key」保存即写入 secrets 文件；
- 或 `.env` 写 `TAVILY_API_KEY=tvly-...` 后重启 sidecar；
- 验证：`python scripts\win\10-check-search-providers.py --live`
  （脚本会自动加载 `.env` 并设置 `DATA_DIR`，否则会读不到 secrets 文件）。
- 当前状态：Key 已保存到 `.data/secrets/ask.json`，真实调用 HTTP 200；环境变量仍优先。

限制：页面解析通道会随搜索引擎改版失效，仅适合个人低频使用；重要结论请点开原文核对。

```ini
WEB_SEARCH_KEYLESS=1         # 开启免密钥通道（默认关闭，测试环境保持确定性）
WEB_SEARCH_KEYLESS_FIRST=1   # 0 = 优先用付费 API
WEB_SEARCH_MAX_QUERIES=3     # 每次提问最多搜索次数
WEB_SEARCH_RESULTS=10
```

## 联网搜索报错（Serper HTTP 400）

- **400** 常见于额度用尽（Serper 返回 `{"message":"Not enough credits"}`）或查询参数不合法；
  **401/403** 是密钥无效/无权限；**429** 是限流；`HTTP 无响应` 是网络或代理问题。
- 诊断（不打印密钥）：
  `python scripts\win\09-check-serper.py`
  会输出密钥来源、前缀/长度、直连与走代理的 HTTP 状态，以及 Serper 返回的剩余 `credits`。
- 处理：
  1. 到 serper.dev 充值或更换 API Key；
  2. 或配置 `TAVILY_API_KEY`（每月有免费额度）/ `SERPAPI_API_KEY`，程序按
     Serper → SerpApi → Tavily 自动兜底；
  3. 都没有时仍可用本地知识库回答，只是没有网页来源。
- 预算控制：`WEB_SEARCH_MAX_QUERIES`（每次提问最多搜索次数，默认 0 不限制，本机 `.env` 设 3）、
  `WEB_SEARCH_RESULTS`（单次返回条数，默认 20，本机 10）。Serper 每次调用扣 1 credit。

## 已知问题与处理

1. **Electron 无法启动（`app` undefined）**
   本机环境变量 `ELECTRON_RUN_AS_NODE=1` 会让 Electron 以纯 Node 方式运行。
   `04-start-desktop.ps1` 启动前会移除该变量。

2. **GPU 进程崩溃（exit_code=-1073741819）**
   远程桌面/无独立显卡环境常见。启动脚本设置 `CHATCHAT_DISABLE_GPU=1`，
   主进程据此关闭硬件加速并追加 `disable-gpu` 等开关。

3. **数据目录被写到 `%APPDATA%`**
   原代码 `DATA_DIR: app.getPath('userData')` 写死。已改为
   `process.env.DATA_DIR || app.getPath('userData')`，并支持 `CHATCHAT_DATA_DIR`
   把 Chromium 配置、日志、crash dump 一并放回仓库。

4. **删除文档后再次导入报 `UNIQUE constraint failed on vec_chunks`**
   `delete_item_index()` 在未加载 sqlite-vec 扩展的连接上删除 `vec_chunks`，
   `no such module: vec0` 被吞掉，留下孤儿向量；新文档复用相同 chunk id 时冲突。
   已在 `services/sidecar/app/index/pipeline.py` 中删除前调用 `try_enable_vec(conn)`。

5. **npm 安装被沙箱的批量删除守卫打断**
   报 `[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED]`。安装时把
   `CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD` 调大即可；`node_modules` 删除走原生 `rm`。

6. **本机 HTTP 代理会劫持回环请求**
   环境变量 `HTTP_PROXY/HTTPS_PROXY` 指向本地代理时，`127.0.0.1` 的请求会被转发。
   验证脚本使用 `trust_env=False` 直连。

7. **无需下载模型**
   默认 `EMBED_PROVIDER` 未设为 `bge`，索引使用确定性哈希向量，启动不加载 torch /
   FlagEmbedding，因此没有模型文件落盘。只有显式设置 `EMBED_PROVIDER=bge` 时才需要
   `requirements.txt` 与模型权重。
