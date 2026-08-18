// dsh-secret-guard —— DeepSeek Harness 敏感信息防护插件（host-only）。
//
// 问题：agent 常把 API key / Token / 私钥直接写进命令行（`curl -H "Authorization: Bearer sk-..."`、
// `git push` 带 token、`cat .env` 后回显……），密钥随即进入命令历史、工具结果与会话日志——
// 一旦日志被同步/分享，密钥就泄漏了。
//
// 做法：拦截 `tools/pre-execute`，对 bash 参数做密钥模式扫描；命中即**阻止执行**并返回明确
// 错误（fail-closed），agent 会看到"请改用环境变量或文件传递密钥"的提示并自我纠正。
//
// 注意：exec.args 里可能含函数/循环引用（如 agent 句柄），不能整体 JSON.stringify，
// 只提取其中的字符串字段做扫描。
//
// 约束：node: 内置模块；无 @deepseek-ai/* 依赖；可配置（DSH_SECRET_GUARD_PATTERNS 追加）。

export const name = 'dsh-secret-guard'

const BUILTIN_PATTERNS = [
  // OpenAI / DeepSeek 风格 key
  { id: 'sk-key', re: /\bsk-[A-Za-z0-9]{16,}\b/ },
  // Google API key
  { id: 'google-api-key', re: /\bAIza[0-9A-Za-z_-]{20,}\b/ },
  // GitHub PAT / fine-grained token
  { id: 'github-token', re: /\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})\b/ },
  // Slack token
  { id: 'slack-token', re: /\bxox[baprs]-[A-Za-z0-9-]{20,}\b/ },
  // AWS access key
  { id: 'aws-key', re: /\bAKIA[0-9A-Z]{16}\b/ },
  // 私钥块
  { id: 'private-key', re: /-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----/ },
  // JWT（两段 base64url + 点）
  { id: 'jwt', re: /\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/ },
]

function loadPatterns() {
  const extra = process.env.DSH_SECRET_GUARD_PATTERNS
  const patterns = [...BUILTIN_PATTERNS]
  if (extra) {
    for (const part of extra.split(',')) {
      const p = part.trim()
      if (!p) continue
      try {
        patterns.push({ id: 'custom', re: new RegExp(p) })
      } catch {
        // 忽略非法正则
      }
    }
  }
  return patterns
}

// 只提取可扫描的字符串，避免 exec.args 里的函数/循环引用导致序列化抛错
function safeBlob(args) {
  if (args == null) return ''
  if (typeof args === 'string') return args
  if (typeof args === 'number' || typeof args === 'boolean') return String(args)
  if (Array.isArray(args)) return args.map(safeBlob).join(' ')
  if (typeof args === 'object') {
    const parts = []
    for (const k of Object.keys(args)) {
      const v = args[k]
      if (typeof v === 'string') parts.push(v)
      else if (typeof v === 'number' || typeof v === 'boolean') parts.push(String(v))
      else if (Array.isArray(v)) parts.push(v.map(safeBlob).join(' '))
      else if (v && typeof v === 'object') {
        try { parts.push(JSON.stringify(v)) } catch { /* 忽略不可序列化对象 */ }
      }
    }
    return parts.join(' ')
  }
  return ''
}

export function apply(ctx) {
  const patterns = loadPatterns()
  ctx.on('tools/pre-execute', async (exec, next) => {
    if (exec.name !== 'bash') return next()
    // 注意：真实 harness 传的是 exec.arguments（{command, description}），不是 exec.args
    const blob = safeBlob(exec.arguments ?? exec.args)
    const hits = patterns.filter(p => p.re.test(blob)).map(p => p.id)
    if (hits.length === 0) return next()
    const list = [...new Set(hits)].join(', ')
    throw new Error(
      `[secret-guard] 检测到疑似敏感信息（${list}），已阻止该命令，防止密钥泄漏进日志。` +
      '请改用环境变量或文件传递密钥，不要把密钥写进命令行。'
    )
  }, { prepend: true })
}
