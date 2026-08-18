# 我给 DeepSeek Harness 搭了一个多后端 Agent 控制面

> 发布前替换：`[你的名字]`、`[你的 GitHub 链接]`、`[仓库地址]`、`[演示视频链接]`
> 标签建议：#DeepSeek #Agent #开源 #LLM应用 #AI工程

---

最近 DeepSeek 开源的 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 挺火——一个"一切皆插件"的 agent 框架。我花了几周时间，在它的 Python SDK 之上做了一个**多后端 agent 控制面**（[仓库地址]），顺手养了一只会干活会睡觉的"大肥鱼"桌宠。这篇文章讲讲我踩过的坑和几个有意思的设计。

## 为什么不做官方的 Web GUI？

DeepSeek Harness 自带官方 Web GUI，聊天体验很好。但我的需求是**管理多个 agent 实例**：同时挂几个 runtime 子进程、实时看每个 agent 在干什么（推理、工具调用、子代理）、对关键操作（bash）做人工审批、然后还能一键停止卡死的任务。官方 GUI 是单实例聊天，不做这些事。所以我用它的 Python SDK（JSON-RPC over stdio）自己搭了一个控制面。

## 第一个坑：审批通道根本不存在

SDK 协议里只有 `initialize / session/prompt / shutdown` 三个方法、四种通知。**"agent 要执行 bash 需要人工批准"这个通道，协议里没有。** 官方路径要靠沙箱升级触发，而默认 runtime 没挂沙箱执行器。

我的解法是一个**文件桥**：在 runtime 里塞了一个小插件（`bridge-plugin/`），拦截 `tools/pre-execute`——agent 每次要执行 bash 就暂停，往 `.approval-bridge/` 写一个 `pending-xxx.json`；server 看到文件就弹审批窗；你点"允许"，server 写 `decision-xxx.json`；插件读到就放行。两个进程没法直接说话？那就传纸条。超时 120 秒自动 fail-closed。

体验上还做了"信任会话"：点一次"允许本会话全部"，之后该会话的 bash 自动放行，不打扰。

## 第二个坑：事件流能把页面卡死

harness 的 agent 一次任务能喷 300+ 条流式推理分片（`assistant/chunk`）。我一开始每条事件都全量重渲染页面，结果浏览器直接"无响应"。修复是三条：

1. **chunk 增量路径**：推理分片只往一个小框里追加文本，不动整个 DOM
2. **渲染节流**：非 chunk 事件合并成 120ms 一批
3. **窗口上限**：对话区只渲染最近 200 条、事件流 300 条（数据全在内存，DOM 有界）

卡顿从"每 300 次全量重建"降到"约零次"，量级差两个数量级。

## 第三个设计：多后端统一抽象

现在这些 agent 平台都是"跑一个命令、输出文本/JSON"：`codex exec --json`、`claude -p --output-format json`、`gemini -p`、`qwen-code -p`、`aider --message`。我把它们抽象成**通用 CLI 后端**——一张配置表定义每个平台的命令和安装提示，输出统一翻译成 `session.event` 流。于是同一套 UI、审批、桌宠逻辑，六个平台无缝复用。加新平台 = 加一行配置。

## 顺手做的好玩的东西

**大肥鱼桌宠**：DeepSeek 的鲸鱼娘二创素材（[dafeiyu-pet](https://github.com/1190fasheqi/dafeiyu-pet)，MIT）——agent 思考时它摇头晃脑、调工具时侧身、等你审批时蹦跳、干完活欢呼、**空闲 45 秒自动打盹 💤**。还支持把文件拖到它身上，它会让 agent 处理。另外接了一个 Live2D 模式（复用 [dsh-live2d-pet](https://www.npmjs.com/package/dsh-live2d-pet) 的免费模型）。

## 现在的功能全景

- **多后端控制面**：harness / codex / claude / gemini / qwen / aider，独立 runtime 子进程增删切换
- **实时观测**：事件流（可筛选/折叠）、工具调用卡片、子代理树、会话刷新重放
- **审批流**：弹窗 + 信任会话 + 超时 fail-closed
- **运营工具**：token 用量统计 + 成本估算告警、会话导出、定时任务（cron）、对话回放（rewind）
- **稳定性**：runtime 崩溃自动重启（自愈）、友好错误提示
- **桌宠**：大肥鱼精灵 + Live2D + 拖文件投喂

## 技术栈

后端纯 Python 标准库（零第三方依赖），前端单文件（零 CDN 依赖），Tauri 2 桌面壳工程可选。所有用到的开源素材/库都是 MIT。

## 下一步

- 给 DeepSeek Harness 提 PR：会话恢复 API（目前从新进程续聊旧会话会被 `id collision` 保护拦住，官方没做）
- 一键安装脚本、原生沙箱审批链路

项目地址：[仓库地址] · 演示视频：[视频链接]

如果你也在做 agent 基础设施，欢迎来 issue 里聊。
