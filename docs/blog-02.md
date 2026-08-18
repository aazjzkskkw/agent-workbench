# DeepSeek Harness SDK 协议考古：审批通道不存在，所以我用文件桥造了一个

> 发布前替换：`[你的名字]`、`[仓库地址]`
> 标签：#DeepSeek #Agent #协议 #JSONRPC #开源

---

在用 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) Python SDK 搭控制面（[仓库地址]）时，我遇到一个"官方协议里不存在"的需求：**agent 执行 bash 前，必须有人工审批**。这篇文章记录我怎么从协议里"考古"出这个结论，以及最后怎么用一张"纸条"（文件桥）把审批流搭出来的。

## 考古：SDK 协议里到底有什么

Harness 的 SDK runtime 是 JSON-RPC over stdio。客户端侧（`client.py`）暴露的能力：

```
请求（客户端 → runtime）：initialize / session/prompt / shutdown
通知（runtime → 客户端）：session.event / session.status / subagent.started / subagent.finished
```

就这么多。**没有任何"runtime 反问客户端要决策"的通道**——没有 approval 请求，没有权限回调。审批能力在 harness 内部是存在的（`dsh-user-approval` 的 `approval/request` 瀑布事件），但它只面向进程内的人（GUI），SDK 模式下的"人"在进程外，够不着。

再考古一下审批什么时候会触发：`dsh-tool-bash` 只在**沙箱升级**（命令被沙箱拒绝、请求放宽）时才走 `ctx.approval`。而默认 runtime 组合根本没挂沙箱执行器（闭包里没有 `dsh-bash-sandbox`）——也就是说默认配置下 **bash 永远不需要审批**。想要审批 = 要么补一套沙箱栈，要么自己造通道。我选了后者。

## 设计：两个进程之间传纸条

思路：审批的"问"和"答"不依赖协议，走文件系统——**两个进程没法直接说话，那就传纸条**。

```
runtime 进程（桥插件）          server.py（工作台）            浏览器
  │ tools/pre-execute 拦 bash      │                            │
  │ 写 pending-<id>.json ────────▶ │ 轮询发现 → 弹审批窗 ─────▶ │
  │                                │ ◀── 你点"允许本会话全部" ──│
  │ ◀─── 读 decision-<id>.json ─── │ 写 decision-<id>.json      │
  │ 放行 / 拒绝                     │                            │
```

桥插件是 runtime 内的一个 host-only 插件（`bridge-plugin/`，纯 node:fs）：

- 监听 `tools/pre-execute`，`exec.name === "bash"` 就进入审批流程
- 写 `pending-<id>.json`（含工具名、原因、会话 id），然后轮询等 `decision-<id>.json`
- 读到 `allow` → `next()` 放行；`deny` / 超时 → 抛错，agent 收到明确的"未获批准"
- **fail-closed**：120 秒没决策 = 拒绝（宁可多拦，不可漏放）

server.py 侧：一个 watcher 线程扫 `.approval-bridge/` 目录，新 `pending-*` 文件 → SSE 推给浏览器弹窗；用户决策 → 写 `decision-*` 文件 + 广播。信任会话 = 记住 session id，之后该会话的 bash 在 watcher 里直接自动写 allow，不再打扰。

## 踩到的两个真坑

**坑 1：runtime 被杀后残留的"幽灵审批"。** 用户点"停止"会把 runtime 子进程杀掉，桥插件的 `finally` 清理没机会跑，`pending-*.json` 留在磁盘上。重启后的 watcher 会把它当新审批广播——一张永远没人答的幽灵卡。修法：按**文件年龄**强制结算（130 秒过期即标记 unavailable 并清理），而不是等文件消失。

**坑 2：harness 把错误包在回合里。** 恢复旧会话时会话持久化报 `id collision`，但 harness 不会让 `session/prompt` 抛异常——它把这个错误写进 `turn/end` 事件的 `reason`，然后照常发 `session.status: idle`。我一开始以为"成功了"，直到发现事件流里只有 turn/start 和 turn/end。修法：worker 里跟踪最后一个 `turn/end` 的 reason，`kind === "error"` 就把 run 标成失败并透传友好信息。

## 收获

1. **协议没有的能力，可以在两侧各加半层**——前提是"人"在进程外，那就把"纸条"递出去
2. **fail-closed 审批**：宁可让 agent 停下来等你，也不能让它未经同意动 shell
3. 文件桥方案零依赖、可审计（磁盘上就是审批记录）、不侵入 harness 源码——将来官方补了审批通道，替换掉桥插件即可

## 下一步

- 给 DeepSeek Harness 提 PR：SDK 协议的审批通道 + 会话恢复 API（`id collision` 说明从新进程续聊旧会话官方还没做）

代码：[仓库地址]/tree/main/bridge-plugin
