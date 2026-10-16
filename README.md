# 知识引擎

这是一个装在你自己电脑上的问答程序。你可以把笔记放进本地库，也可以打开「提问」：程序会先把没说清的地方问完，再从本地笔记、ima 知识库和网上资料里找依据，并在回答里标出每句话的来源。

日常使用看这一页。接口、测试和目录说明在 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。

## 你需要准备

- Node.js 22
- Python 3.11 或 3.12
- 可选的密钥：DeepSeek（用来组织和写回答）、Serper（用来上网搜索）。没有搜索密钥时，仍可以导入本地文件并离线回答。要用 ima 知识库，还需要 ima 的 Client ID 和 API Key。

密钥只放在你自己的电脑上。仓库里只跟踪 `.env.example`。把它复制成 `.env` 后再填写；`.env`、`.env.local` 和 `secrets/*.json` 不会被提交。下面的示例账号是虚构的 `you@example.com`，问题用「某地附近的餐厅推荐」和「示例概念是什么」。请换成你自己的密钥，不要把真密钥写进仓库。

## 安装

在仓库根目录打开终端。

安装桌面程序：

```bash
npm install
```

安装后台（问答、导入、搜索都由它完成）：

```bash
cd services/sidecar
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements-core.txt
```

Windows PowerShell：

```powershell
cd services\sidecar
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -r requirements-core.txt
```

`requirements-core.txt` 足够启动。只有在你明确要使用 `EMBED_PROVIDER=bge` 时，才需要再安装带 torch 的 `requirements.txt`。

## 启动

回到仓库根目录。下面这一条会打开窗口，并自动启动后台。`VITE_FEATURE_ASK` 打开窗口里的提问；程序会把它传给后台。

macOS 或 Linux：

```bash
export VITE_FEATURE_ASK=true
npm run dev
```

Windows PowerShell：

```powershell
$env:VITE_FEATURE_ASK="true"
npm run dev
```

想连 ima 一起用，再加一个开关（同样要在 `npm run dev` 之前设置）：

```bash
export VITE_FEATURE_ASK=true
export VITE_FEATURE_IMA=true
npm run dev
```

`VITE_FEATURE_CLARIFY` 是另一块独立的澄清面板。提问流程自己会追问，不依赖这个开关。

如果要单独启动后台（窗口不会出现），在 `services/sidecar` 里、虚拟环境已激活时执行：

```bash
export SIDECAR_TOKEN=devtoken
export DATA_DIR="$PWD/../../.data"
export FEATURE_ASK=1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

`FEATURE_ASK=1` 和 `VITE_FEATURE_ASK=true` 是同一个功能：前者给单独启动的后台，后者给桌面窗口。用 `npm run dev` 时设置 `VITE_FEATURE_ASK` 即可。

## 填写密钥

真密钥只留在本机的 `.env` 或数据目录里的密钥文件中，不要提交到 git。

**方式一：复制示例文件，再在当前终端里加载**

程序不会自动读取仓库根目录的 `.env`。复制并改好之后，在启动前于同一个终端里加载：

```bash
cp .env.example .env
set -a
source .env
set +a
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
Get-Content .env | ForEach-Object {
  if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
  $name, $value = $_.Split('=', 2)
  Set-Item -Path "Env:$($name.Trim())" -Value $value.Trim()
}
```

`.env.example` 里的账号是 `you@example.com`，密钥都写成 `replace-me`。打开 `.env`，把 `replace-me` 换成你自己的，然后再执行上面的加载命令。也可以不写文件，在启动前临时设置：

```bash
# 虚构账号 you@example.com
export DEEPSEEK_API_KEY=replace-me
export SERPER_API_KEY=replace-me
export IMA_CLIENT_ID=replace-me
export IMA_API_KEY=replace-me
```

上网搜索也可以改用 `SERPAPI_API_KEY` 或 `TAVILY_API_KEY`。三个搜索密钥里有一个即可。

**方式二：写在本机文件里**

单独启动后台时，`DATA_DIR` 就是你上面设置的目录。在里面新建两个文件（只给自己读）：

`$DATA_DIR/secrets/ask.json`

```json
{
  "deepseek_api_key": "replace-me",
  "serper_api_key": "replace-me"
}
```

`$DATA_DIR/secrets/ima.json`

```json
{
  "client_id": "replace-me",
  "api_key": "replace-me"
}
```

这两个文件在本机数据目录里，不要提交。示例账号仍是 `you@example.com`。

**方式三：写在窗口里**

打开提问之后，窗口里有「问答密钥」，可以填写 DeepSeek、Serper、SerpApi、Tavily。打开 ima 面板后，可以填写 Client ID 和 API Key。保存后只留在本机，不会写进知识库数据库，界面也不会把已保存的密钥再显示出来。

环境变量优先于文件。用桌面窗口时，优先用方式一或方式三，不必去猜数据目录在哪。

ima 的 Client ID 和 API Key 从 [https://ima.qq.com/agent-interface](https://ima.qq.com/agent-interface) 获取。使用官方密钥，不要粘贴登录 Cookie。

## 怎么用

1. 在输入框写下问题。示例账号 `you@example.com` 可以问「某地附近的餐厅推荐」，或问「示例概念是什么」，然后发送。
2. 如果范围还没说清，程序会一次问一件事。有选项时点其中一个；要改说法就在框里输入。地点题没有预设答案，需要你写清楚或从列出的候选里选。
3. 回答里的 `[1]`、`[2]` 是引用。点引用可以看摘录，并知道它来自本地文件、ima 知识库还是网页。
4. 只想查已经导入的文档时，可以不打开提问。先在左侧导入 md、txt、pdf 或 docx，再在输入框提问。库里没有相关内容时，会直接说明没有找到。

本地生活类问题（餐厅、附近、出游）默认查网页。如果你点名要查笔记、本地文件或某个知识库，才会去查那些库。

## 常见问题

**窗口里还是只能查已导入的文档**

启动前没有设置 `VITE_FEATURE_ASK=true`。关掉窗口，在同一个终端里先设置变量，再执行 `npm run dev`。

**提示没有配置联网搜索密钥**

填写 `SERPER_API_KEY`，或改用 `SERPAPI_API_KEY`、`TAVILY_API_KEY`。没有 DeepSeek 密钥时，程序仍会尽量用摘录回答，但不会调用模型组织长回答。

**回答写着「没找到可引用的来源」**

找到的材料里没有能对上这句话的句子。换一种问法，或先把相关笔记导入本地库。

**地点题无法直接采用空白推荐**

地点必须由你选定。从选项里点一个，或自己写上具体地点。

**网上没有实时评分**

程序不会编造评分。需要分数时，请以大众点评 App 为准；摘录里写了的分数才会出现在回答里。

**`npm install` 或 `npm run dev` 失败**

确认 Node.js 是 22：`node -v`。

**后台装不上或启动报错**

确认 Python 是 3.11 或 3.12：`python3 --version`。在 `services/sidecar` 里使用上面的虚拟环境，并已执行 `pip install -r requirements-core.txt`。

**单独访问后台时返回 401**

请求要带上和 `SIDECAR_TOKEN` 相同的头 `x-sidecar-token`。用桌面窗口时，程序会自己带上，不用手填。
