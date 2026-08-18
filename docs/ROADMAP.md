# 🗺 完善路线图 —— 想加功能？从这里选

> 使用方式：按"价值 / 难度"排序。每实现一个，把 `[ ]` 改成 `[x]` 并写一行"怎么验证的"。
> 提新需求：把想法写进 `docs/IDEA.md`（没有就建一个），或直接告诉工作台里的 agent 帮你实现。

## 当前状态（v0.5，2026-08）

已实现：多后端控制面（harness/codex/claude/gemini/qwen/aider）、实时事件流、审批流（文件桥+信任）、
大肥鱼桌宠（精灵+Live2D+拖文件投喂）、runtime 自愈、用量统计、会话导出、会话搜索、可折叠侧栏、J-Space 集成。

## 候选功能（按优先级）

### P0 —— 补核心体验
- [x] **磁盘会话恢复**：服务重启后从 `sessions/<runtime名>/` 扫描恢复会话列表（验证：重启后 memory-demo 自动出现）
  - ⚠️ 限制：harness 不允许从新进程续聊旧会话（`id collision` 保护）——现在会给出友好提示"请新建会话"；
    实现"真·续聊"需要给 deepseek-harness 提 PR（会话恢复 API），是很好的开源贡献题材
- [x] **技能目录浏览 UI**：从会话的 skill 注入消息解析 `<available_skills>`，右侧"技能"tab 列出 skills + "让 agent 加载"一键调用（验证：真实目录解析出 j-space 等，格式回归测试 PASS）
- [x] **工具结果 diff 高亮**：bash 输出检测为 diff 时红绿行级高亮（`+`绿 `-`红 `@@`青 `diff --git`灰），普通输出不误判（验证：diff 文本 true / ls 输出 false PASS）

### P1 —— 运营向
- [x] **定时任务调度**：参考 OpenClaw 的 cron——"每天 9 点跑 XX prompt"（后台线程 + 配置）
  - 支持 `interval_minutes`（间隔）与 `daily "HH:MM"`（每日）两种模式；JSON 持久化（schedules.json）
  - 验证：3 秒间隔任务自动触发（last_run 更新 + 会话 204 条事件）；暂停/删除 API 均 PASS
- [x] **对话回放时间轴**：参考 OpenHands rewind——会话栏"⏪ 回放"进入回放模式，滑块拖动回看任意一步；
  切片渲染（工具卡片会停留在当时的执行状态）；切换会话自动退出回放（验证：真实会话 50% 切片 PASS）
- [x] **成本告警**：统计面板估算成本超阈值时弹 toast + 卡片标红；阈值可调（localStorage 记忆，参考 Langfuse budget）
  - 验证：越界→toast、未回落不重复、回落后再越界再次 toast（去重逻辑 PASS）

### P2 —— 体验打磨
- [ ] 暗/亮主题切换（localStorage 记忆）
- [ ] 事件流时间轴模式（按时间线展示，替代纯列表）
- [ ] 会话拖拽排序 / 分组（按 runtime）
- [ ] 移动端适配（窄屏自动折叠侧栏）

### P3 —— 发布与生态
- [ ] Tauri 壳完成生产构建（内嵌静态资源 + 一键启动服务）
- [ ] 一键安装脚本（clone harness → 构建 runtime 载体 → 启动，`install.sh`）
- [ ] 接入 deepseek-harness 官方 web GUI 风格指南，统一视觉

## 已完成记录

- 2026-08-18 v0.1：多 runtime 控制面 + 审批 + 事件流
- 2026-08-18 v0.2：大肥鱼桌宠（精灵）
- 2026-08-18 v0.3：Live2D 模式
- 2026-08-18 v0.4：通用 CLI 后端（6 平台）
- 2026-08-18 v0.5：自愈 + 统计 + 导出 + 喂食 + 布局改善
