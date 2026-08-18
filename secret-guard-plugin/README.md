# dsh-secret-guard

DeepSeek Harness 敏感信息防护插件（host-only）：拦截 agent 写入 bash 命令行的疑似密钥/Token，命中即**阻止执行**并提示改用环境变量或文件传参，防止密钥泄漏进 shell 历史、工具结果与会话日志。

## 安装

```sh
# 从 npm 安装（bundle 包）
dsh plugin --profile <name> add dsh-secret-guard

# 或从 GitHub 安装（需在 profile 的 pnpm-workspace.yaml 里 allowBuilds 该包）
dsh plugin --profile <name> add github:you/dsh-secret-guard
```

## 功能

- 拦截 `tools/pre-execute`，扫描 bash 参数中的密钥模式；
- 命中即抛错阻止执行（**fail-closed**），agent 会看到"请改用环境变量或文件传递密钥"并自我纠正；
- 可配置扩展模式列表。

内置检测模式：

| 模式 | 示例 |
|---|---|
| `sk-key` | `sk-...`（OpenAI / DeepSeek API key） |
| `google-api-key` | `AIza...` |
| `github-token` | `ghp_...` / `gho_...` / `github_pat_...` |
| `slack-token` | `xoxb-...` / `xoxa-...` |
| `aws-key` | `AKIA...` |
| `private-key` | `-----BEGIN ... PRIVATE KEY-----` |
| `jwt` | `eyJ...eyJ...` 三段式 JWT |

## 配置

环境变量 `DSH_SECRET_GUARD_PATTERNS`：追加自定义正则（逗号分隔），例如：

```sh
DSH_SECRET_GUARD_PATTERNS='my-secret-pattern-\w+'
```

## 局限

- 本版拦截"写入命令行的密钥"这一最高频泄漏路径；工具**输出侧**脱敏（结果打码）依赖 harness 的 `finalizeContent` 机制，后续版本提供。

## License

MIT
