# M7 改造说明（i18n · 导入通道 · 文体适配 · 对话记忆）

本轮在既有「知识引擎」上做四项增量改造，并新增集中的设置界面。所有原有接口保持兼容。

## 一、界面国际化

**范围**
- 全部界面文案：外壳（标题、导航、状态）、知识库、问答、澄清、ima、决策层、对话、设置。
- 语言切换按钮在顶栏（中文 / EN），切换即时生效，无需刷新。
- 持久化两层：`localStorage["chatchat.locale"]`（本机即时）+ `POST /settings/ui`（写入 `DATA_DIR/config/ui.json`，换浏览器/重启后恢复）。
- 支持深链：`?lang=en`、`?lang=zh&view=settings`、`#conversations`。

**实现要点**
- `apps/desktop/src/renderer/src/i18n/index.tsx`：Provider + `useI18n()`，暴露 `{ locale, setLocale, t, tf }`。
- 词典以**中文原文为 key**（`i18n/dictionary.ts`），`t('中文')` 命中英文；`zh` 模式直接返回 key，因此中文永不漂移。
- `tf('当前 {tokens} tokens / 上限 {limit}', {...})` 处理插值。
- 后端按 `body.locale` 或 `X-Locale` / `Accept-Language` 输出系统文案（未命中、流式错误、占位提示），并在提示词里切换回答语言：`services/sidecar/app/i18n.py`。

## 二、知识库导入扩展（统一通道）

**范围**
- 保留 `/library/import`（兼容）；新增统一入口 `/import/documents`。
- 通道注册表：`local`（已接入）、`ima`（已接入，走原面板）、`obsidian` / `feishu` / `tencent_docs` / `notion`（占位，声明能力与所需配置）。
- 本期只做接口抽象与占位，不做真实对接。

**协议**（`services/sidecar/app/imports/protocol.py`）
```python
class ImportSource:
    descriptor: SourceDescriptor        # id / 名称 / implemented / configured / config_fields / docs_url / ui
    def status(locale) -> dict
    def browse(parent_id, locale) -> list[ContainerRef]      # vault / 知识库 / 文件夹
    def fetch(item_id, locale) -> ImportDocument             # 统一文档对象（text 或 bytes + metadata）
```
新通道只需实现上述类并调用 `registry.register(source)`，路由与前端无需改动。

**接口**
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/import/sources` | 列出全部通道及 implemented/configured |
| GET | `/import/sources/{id}` | 单通道详情 |
| POST | `/import/sources/{id}/browse` | 列出容器；占位通道返回 501 + `{code,message,missing}` |
| POST | `/import/sources/{id}/import` | 拉取所选条目；未实现/未配置返回结构化 501/400 |
| POST | `/import/documents` | 归一化导入（filename + text/base64 + source/external_id） |

## 三、输出文体适配

**范围**
- 按内容自动判定 5 类：life / knowledge / technical / design / general。
- 14 种文体（中英各半），例如：小红书笔记体、海外社媒短文、知乎回答体、教科书体、调研报告体、操作说明书体、技术方案体、专业设计说明体。
- `/ask` 与 `/chat` 均新增 `style`（`auto` 或具体 id）与 `locale`；`done` 事件回传实际命中的 `style`，前端在回答旁显示文体徽章。

**实现要点**
- `services/sidecar/app/styles.py`：关键词打分分类 + 文体目录 + `directive()`（注入提示词）+ `format_offline()`（离线抽取式回答仅在显式指定「操作说明书」时做编号化，自动模式不改写正文）。
- 提示词注入点：`app/api/chat.py`（`compose_answer(style_directive=...)`）、`app/ask/synthesize.py`（`_messages`/`_regenerate_thin` 透传）。
- 跨语言引用：英文回答引用中文证据时，`citation_supports` 允许「译文句 + 已存在的 [n]」，避免整段答案被引用校验清空。

## 四、对话记忆管理

**范围**
- 对话保存、文件夹归类、项目归类、置顶/重命名/删除、上下文超限提示与分叉、单条/全量导出、对话导入。
- 导入格式：ChatGPT 导出（`conversations[].mapping` 树）、Claude 网页导出（`chat_messages`）、Claude Code JSONL、通用 `messages` 数组、Markdown/纯文本转录。

**数据模型**（迁移 `0004_m7`）
- 新表：`project`、`conversation_folder`。
- `conversation` 新增：`folder_id`、`project_id`、`forked_from_id`、`forked_from_message_id`、`locale`、`style`、`updated_at`、`archived`、`pinned`。

**接口**
| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/conversations` | 列表（folder/project/q/archived/分页）/ 新建 |
| GET/PATCH/DELETE | `/conversations/{id}` | 详情（含 context）/ 改名、归类、置顶、归档 / 删除 |
| GET | `/conversations/stats` | 对话、消息、分叉数、token 估算 |
| POST | `/conversations/{id}/messages` | 追加一条消息 |
| POST | `/conversations/{id}/fork` | 按 `message_id` 复制前缀生成分支，记录 `forked_from_id` |
| GET | `/conversations/{id}/export?format=json\|md` | 单对话导出 |
| GET | `/conversations/export?format=json\|md` | 全量导出 |
| POST | `/conversations/import` | 自动识别格式导入 |
| POST | `/conversations/clear` | 清空全部对话（需 `confirm=DELETE_ALL`），自动备份后删除对话/消息/文件夹/项目/澄清会话/决策日志 |
| GET/POST | `/folders`、`/projects` | 文件夹 / 项目管理；`PATCH`、`DELETE /{id}` |

**上下文分叉**
- 阈值 `CONTEXT_FORK_TOKENS`（默认 6000），`GET /conversations/{id}` 返回 `context.needs_fork`。
- 前端在详情页显示 token 进度与「超出上限，建议分叉」，并支持在任意一条消息处「从第 n 条分叉」。

## 五、设置界面

- 独立「设置」标签页：界面语言、默认文体、密钥（DeepSeek / Serper / SerpApi / Tavily）、数据与存储（数据目录、库大小、版本、功能开关、分叉阈值）、导入导出、样式目录。
- 新增 `GET /settings/app`、`GET/POST /settings/ui`、`GET /styles`；密钥仍走既有 `/settings/ask`，只回显「已配置/未配置」，不返回明文。

### Electron 桥接白名单

桌面端的 renderer 不直接访问网络，所有请求经 `apps/desktop/src/shared/sidecarPaths.ts` 的
`allowedSidecarPath()` 过滤后才由主进程转发。新增接口必须同步登记，否则在 Electron 里会被
静默拒绝（浏览器版不受影响，这也是两个界面表现不一致的常见原因）。本轮已放行：

- `GET/POST/PATCH/DELETE /conversations*`、`/folders*`、`/projects*`
- `POST /import/documents`、`GET/POST /import/sources*`
- `GET/POST /styles`、`/settings/app`、`/settings/ui`

## 六、M8 补充（本地优先检索 / 可点击来源 / 续聊 / 1M 上下文）

1. **未指定知识库也检索本地库**
   `_route_sources()` 现在会把 `local` 补进通道列表；只有「附近美食 / 旅游」这类生活即时问题
   （且没有点名知识库）才只查网页。指定 `kb_name` 时会额外走 ima。
   提问「Dify」时会同时命中本地文档与网页，并在回答里分别标注 `[n]`。

2. **网页结果可直接打开**
   - 后端：citation 保留 `url`（http/https）。
   - 前端：新增 `lib/openLink.ts`，Electron 走 `shell.openExternal`，浏览器走 `window.open`；
     引用 chip 旁提供 `↗` 按钮，引用详情里显示可点击的完整 URL，对话详情里的来源也可点击。

3. **对话续聊**
   - 对话详情底部新增输入框，提交后调用 `/ask` 并带上 `conversation_id`，回答追加到同一对话。
   - `/ask` 会读取该对话最近 6 条消息：短追问含「它/这个/该…」时，把上一轮用户问题拼进检索
     query，同时把对话历史写进提示词，保证「它支持哪些模型？」这类追问能接上上下文。
   - 详情页可查看全部历史消息、来源与 token 用量，并可继续提问。

4. **上下文上限提高到 1M**
   `CONTEXT_FORK_TOKENS` 默认值与 `.env` 均改为 `1000000`，`/settings/app` 与对话详情同步返回。

验收脚本：`scripts/win/08-verify-context.py`（11 项：本地命中、local 引用、可点击 URL、
续聊追加、1M 上限）。

## 七、验收标准与结果

| # | 验收标准 | 结果 |
|---|---|---|
| 1 | 中英文可一键切换，全部界面文案即时变化 | 通过（截图 + `data-locale`） |
| 2 | 语言选择刷新/重启后保留 | 通过（localStorage + `DATA_DIR/config/ui.json`） |
| 3 | 导入通道列表含 4 个占位通道，调用返回结构化 501 | 通过 |
| 4 | 统一导入接口可写入并索引文档 | 通过（`POST /import/documents`） |
| 5 | 5 类内容可自动匹配文体；显式指定可覆盖 | 通过（`/styles` + `done.style`） |
| 6 | 英文模式回答为英文并保留引用 | 通过（DeepSeek 实测） |
| 7 | 对话可保存、进文件夹、进项目、置顶、删除 | 通过 |
| 8 | 超阈值提示分叉；分叉保留前缀与来源 | 通过（`forked_from_id`） |
| 9 | 导出 JSON/Markdown；导入 ChatGPT/Claude Code 记录 | 通过（各 1 条） |
| 9b | 一键清空全部对话，清空关联状态并返回计数与备份路径 | 通过（`07-verify-purge.py` 13 项） |
| 10 | 设置页集中管理密钥与存储信息，不回显明文 | 通过 |
| 11 | 原有功能与单元测试不受影响 | 通过（前端 17/17；后端 99 passed） |
| 12 | 未指定知识库时本地库仍参与检索并产生本地引用 | 通过（`08-verify-context.py`） |
| 13 | 网页引用带可点击 URL，Electron 与浏览器都能打开 | 通过 |
| 14 | 对话详情可看全部记录并继续追问，回答追加到同一对话 | 通过 |
| 15 | 上下文上限 1,000,000 tokens | 通过 |

自动化脚本：`scripts/win/06-verify-m7.py`（21 项断言，全绿）。

后端 `pytest` 全量 101 项中 99 通过，2 项失败为 Windows 平台断言差异，与本轮改动无关：

- `test_upgrade_existing_v1_database`：断言 `database_url()` 以 `data/app.db` 结尾，Windows 返回反斜杠路径。
- `test_ask_secrets_stay_out_of_the_response`：断言密钥文件权限为 `0o600`，Windows 不实现 POSIX 文件模式。
