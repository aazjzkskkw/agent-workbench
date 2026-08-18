# Agent Workbench — 原生桌面壳（Tauri 2）

把本地运行的 Workbench Web UI（`http://127.0.0.1:8787`）包成原生桌面窗口。
Tauri 2 + 系统 WebView，体积小、内存占用低。**壳本身不做业务逻辑**——
agent 运行、事件流、审批都在 Python 服务端（`../server.py`）里。

## 前提

1. **Rust 工具链**（本机安装一次）：https://rustup.rs （`curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`）
   - WSL2：需要 WSLg 才能显示窗口（Windows 11 默认支持；Windows 10 需装 WSLg）。
   - 更省事的方式：在 Windows 侧（如 VS Code 的 WSL 插件或 PowerShell）跑 `pnpm tauri dev`，窗口直接显示在 Windows 桌面。
2. **pnpm**（已有）。

## 运行（开发模式）

```bash
# 终端 1：启动工作台服务（后端 + agent runtime）
cd .. && ./run.sh

# 终端 2：启动桌面壳
cd desktop
pnpm install
pnpm tauri dev
```

第一次构建会编译整个 Tauri + WebView 依赖，约 3–10 分钟（取决于网络与机器）。

## 打包（可选）

```bash
pnpm tauri icon path/to/icon.png   # 先生成应用图标（tauri build 需要）
pnpm tauri build                   # 产出 deb/rpm/AppImage（Linux）或 NSIS/MSI（Windows）
```

## 结构

```
desktop/
├── package.json              # @tauri-apps/cli 脚本
└── src-tauri/
    ├── Cargo.toml            # Rust 依赖（tauri 2）
    ├── tauri.conf.json       # 窗口配置：1280×820，url 指向本地工作台
    ├── capabilities/default.json  # 权限：仅 core:default（纯 WebView 壳）
    └── src/{main,lib}.rs     # 最小 Rust 入口
```

## 说明与后续

- 当前是"壳 + 本地服务"模式：窗口打开时要求 `./run.sh` 已运行。
- 若要完全自包含（双击即用、无服务进程），后续可把 `../static/` 内嵌进 Tauri
  （`frontendDist` 指向静态目录 + 用 Rust 端启动 Python 服务进程或直连 SDK），
  再配合 `dsh-user-approval` 用 Tauri 系统通知做原生审批弹窗。
