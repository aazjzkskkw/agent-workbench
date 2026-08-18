# Agent Desktop Workbench

本地 **Agent 控制面 / 桌面工作台**：用 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)
Python SDK 协议直连真实 harness runtime 子进程，在浏览器里实时观察、审批、管理多个 agent。

后端纯 Python 标准库（零第三方依赖），前端单文件（零 CDN 依赖），Tauri 桌面壳可选。

> 与官方 Web GUI（单实例聊天）不同，这是**多 runtime 控制面**：同时拉起/切换/关停多个
> 独立 agent 子进程，逐事件可视化（推理流、工具调用、子代理树），并对关键操作（bash）
> 提供人工审批 + "信任会话"策略。

## 📚 文档

- [完善路线图](docs/ROADMAP.md) —— 候选功能与优先级
- [推广计划](docs/PROMOTE.md) —— 发布 GitHub、演示视频、博客、社区
- [工作流程](docs/WORKFLOW.md) —— 学 / 完善 / 推广三套日常流程

## ✨ 功能

| 能力 | 说明 |
|---|---|
| 🎛️ 多 runtime 控制面 | 多个独立 runtime 子进程（各自会话/事件/审批桥），一键增删切换 |
| 🌐 多 agent 后端 | **harness**（DeepSeek Harness 官方 runtime）+ 通用 CLI 后端：**codex** / **claude**（Claude Code）/ **gemini**（Gemini CLI）/ **qwen**（Qwen Code）/ **aider**，同一 UI 驱动 |
| 📡 实时事件流 | SSE 推送全部 `session.event` / `session.status` / `subagent.*`，JSON 可展开 |
| 💬 对话区 | markdown 渲染、🧠 推理折叠、token 用量、流式"正在生成"预览 |
| 🛠 工具调用检查器 | `tool/call ↔ tool/result` 自动配对卡片，参数/结果可展开 |
| 🔐 审批流 | 每次 bash 调用弹出审批卡（允许本次 / **允许本会话全部** / 拒绝），超时 fail-closed |
| 🐋 大肥鱼桌宠 | DeepSeek 鲸鱼娘二创桌宠（素材 MIT 授权）：随 agent 状态表现情绪（思考/工具/审批/完成/出错/空闲睡觉），**拖文件给它即触发处理** |
| 🌲 子代理树 | `subagent.started/finished` 实时构建会话树 |
| ⏹ 停止运行 | 卡死的任务一键终止（runtime 自动重启，会话历史保留） |
| 💾 刷新重放 | 服务端缓存事件流，页面刷新后对话/日志完整还原 |
| 📊 用量统计 | 会话/事件/工具/token（输入·输出·缓存·推理）汇总 + 成本估算（参考 OpenHands/Langfuse 面板） |
| 📤 会话导出 | 一键导出当前会话为 Markdown（`GET /api/runtimes/<id>/sessions/<sid>/export`，参考 OpenHands） |
| 🛡 runtime 自愈 | 子进程意外崩溃自动重启（守护线程，实测杀进程 6 秒复活） |
| 🍥 桌宠喂食 | 点 🍥 喂小鱼干，桌宠开心互动（参考 dafeiyu-pet 原版喂食） |
| 🧠 J-Space 集成 | 可挂载 J-Space Cognition Suite（系统提示注入 + skill 注册） |
| 🖥 桌面壳 | `desktop/` 提供 Tauri 2 原生窗口工程（需本机 Rust） |

## 🐋 桌宠

- **大肥鱼精灵**（默认）：素材来自 [1190fasheqi/dafeiyu-pet](https://github.com/1190fasheqi/dafeiyu-pet)（MIT）——DeepSeek 鲸鱼娘·大肥鱼三视图。
- **Live2D 模式**（可选，点桌宠旁的 🎀 切换）：复用 [dsh-live2d-pet](https://www.npmjs.com/package/dsh-live2d-pet)（MIT）的 Cubism Core + Haru 免费模型，
  引擎为 pixi.js + pixi-live2d-display（均 MIT），全部本地 vendor（`static/vendor/live2d/`），离线可用。
- **状态联动**：agent 思考时摇头晃脑冒气泡、调工具时侧身、等你审批时蹦跳卖萌、干完活欢呼、空闲 45 秒自动打盹（💤）。
- **拖文件投喂**：把文件拖到桌宠上 → 存进 `workspace/` → agent 自动开工处理（`POST /api/runtimes/<id>/files`）。

## 🏗 架构

```
浏览器 UI (localhost:8787)
   │  SSE 事件流 / JSON API（纯 stdlib HTTP 服务）
   ▼
server.py — RuntimeManager
   ├─ RuntimeInstance #1 ── HarnessClient ── dsh runtime 子进程（node, JSON-RPC/stdio）
   ├─ RuntimeInstance #2 ── HarnessClient ── dsh runtime 子进程（node）
   └─ ...  每个实例：会话表 · 事件日志 · 审批桥 · 请求线程
```

- **审批桥**（`bridge-plugin/`，运行时内插件）：`tools/pre-execute` 闸门拦截 bash →
  经 `.approval-bridge/` 文件交换向工作台要决策 → 超时 fail-closed。信任会话后自动放行。
- **运行时组合**（`cordis.yml`）：官方默认 + skills + J-Space + 审批桥。

## 📦 前提

1. **Python 3.10+**（后端仅用标准库；`run.sh` 自动找 `python3`，可用 `PYTHON=` 覆盖）
2. **DeepSeek Harness 检出**（默认位于本目录上一级的 `deepseek-harness/`，可用 `DSH_REPO` 覆盖），并构建好 **node 载体 runtime**：

```bash
cd deepseek-harness
pnpm install
pnpm run build:lib
pnpm --filter dsh-jsonrpc-agent-pkg deploy --legacy --prod \
  --config.node-linker=hoisted --config.auto-install-peers=false \
  --config.link-workspace-packages=true \
  python/sdk-runtime/src/deepseek_harness_runtime/runtime/node
# 将 python/sdk-runtime/node_modules 中缺失的 workspace 包复制进 runtime/node/node_modules
```

3. **DEEPSEEK_API_KEY**（默认读上一级 `.env`，可用 `DSH_ENV_FILE` 覆盖）
4. （可选）把 `bridge-plugin/` 和 `j-space-cognition-suite`（如需要）复制进
   `runtime/node/node_modules/`，`cordis.yml` 已引用它们。
5. （可选）**CLI 后端**（codex / claude / gemini / qwen / aider）：安装对应 CLI 并配置 API key，
   未安装时该后端会优雅报错（含安装提示），不影响其他后端：

| 后端 | 安装 | API Key |
|---|---|---|
| codex | `npm install -g @openai/codex` | `OPENAI_API_KEY` |
| claude | `npm install -g @anthropic-ai/claude-code` | `ANTHROPIC_API_KEY` 或 `claude /login` |
| gemini | `npm install -g @google/gemini-cli` | `GEMINI_API_KEY` |
| qwen | `npm install -g @qwen-code/qwen-code` | `DASHSCOPE_API_KEY` |
| aider | `pip install aider-chat` | `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` |

> 通用 CLI 后端以"一次性运行 + 解析输出"接入（`codex exec --json` / `claude -p --output-format json` /
> `gemini -p` / `qwen-code -p` / `aider --message`），把输出翻译成统一的 session.event 流。
> 环境变量 `<BIN>_BIN` 可覆盖二进制路径（如 `CLAUDE_BIN`），`fake-claude.sh` 是测试夹具。

## 🚀 快速开始

```bash
./run.sh                    # 默认 http://127.0.0.1:8787
# WORKBENCH_PORT=9000 ./run.sh
```

打开浏览器 → 新建会话 → 下达任务 → 第一次 bash 调用弹审批卡 → 点"允许本会话全部" →
之后该会话自动放行，agent 全程无需人工干预。

冒烟测试：`python3 smoke_test.py`（跑一个真实回合验证链路）。

## ⚙️ 配置（环境变量）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `WORKBENCH_PORT` | `8787` | HTTP 端口 |
| `DSH_REPO` | `../deepseek-harness` | Harness 检出目录 |
| `DSH_ENV_FILE` | `../.env` | 读取 API key 的 env 文件 |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | 默认模型（runtime 级可覆盖） |
| `DSH_APPROVAL_GATE` | `bash` | 审批闸门拦截的工具集（逗号分隔） |
| `DSH_APPROVAL_TIMEOUT` | `120000` | 审批超时（毫秒），超时视为拒绝 |

## 🔌 API

```
GET  /api/events                       SSE 事件流（hello 携带全量状态）
GET  /api/state                        {runtimes: {id: {...}}}
POST /api/runtimes                    创建运行时 {name?, model?, cwd?}
POST /api/runtimes/<id>/run           {session_id?, text}
POST /api/runtimes/<id>/approval      {id, decision: allow|deny, trust?}
POST /api/runtimes/<id>/trust         {session_id, trust: bool}
POST /api/runtimes/<id>/respond       {request_id, result}
POST /api/runtimes/<id>/cancel        {session_id}   # 停止运行（runtime 自动重启）
DELETE /api/runtimes/<id>             关闭并移除运行时
```

## 📁 目录

```
server.py          后端（RuntimeManager / RuntimeInstance / HTTP+SSE）
static/index.html  前端（单文件，无外部依赖）
cordis.yml         运行时组合（skills + j-space + 审批桥）
bridge-plugin/     审批桥插件源码（复制进 runtime 闭包 node_modules）
smoke_test.py      SDK 冒烟测试
desktop/           Tauri 2 桌面壳工程（需本机 Rust）
workspace/         agent 默认工作目录（agent 生成的文件）
sessions/          会话持久化（JSONL zstd，运行时数据，不入库）
```

## ⚠️ 已知限制

- 审批闸门默认只拦 `bash`（`DSH_APPROVAL_GATE` 可换工具集）；"信任"按会话生效，新会话需重新确认。
- 原生沙箱升级式审批（tool-bash + `dsh-bash-sandbox`）需要向 runtime 闭包补沙箱执行器，是审批流的 canonical 升级路径，暂未启用。
- 会话/审批数据保存在本地文件，重启服务后会话列表保留、事件流按缓存重放。

## 🗺 Roadmap

- [x] 多后端抽象（harness / codex）
- [x] 大肥鱼桌宠 + 拖文件投喂
- [ ] 事件流虚拟滚动 + 对话区增量 DOM（当前为窗口上限 + 节流渲染）
- [ ] codex 后端实时流式（当前为 `codex exec` 一次性结果）
- [ ] `dsh-user-approval` canonical 审批链路（沙箱升级路径）
- [ ] Tauri 壳内嵌静态资源 + 一键启动服务（免双进程）
- [ ] 会话搜索 / 回放时间轴

## 📄 License

[MIT](LICENSE)
