// workbench-approval-bridge — host-only plugin for the Agent Desktop Workbench.
//
// Two jobs:
//   1. Gate every `bash` tool call on an explicit human decision (deterministic
//      approval flow for the workbench; the canonical sandbox-escalation path
//      needs a sandboxing bash executor that the bundled closure does not ship).
//   2. Exchange the question/answer with the workbench server through a small
//      file bridge ($DSH_APPROVAL_BRIDGE): the plugin writes `pending-<id>.json`,
//      the server UI writes `decision-<id>.json`, the plugin polls and settles.
//
// Constraints: node: builtins only, no @deepseek-ai/* imports, fail closed
// (timeout → 'unavailable', which denies the call).
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import path from 'node:path'

export const name = 'workbench-approval-bridge'

const BRIDGE_DIR = process.env.DSH_APPROVAL_BRIDGE ?? '.approval-bridge'
const GATED_TOOLS = new Set((process.env.DSH_APPROVAL_GATE ?? 'bash').split(',').map(s => s.trim()).filter(Boolean))
const POLL_MS = 250
const TIMEOUT_MS = Number(process.env.DSH_APPROVAL_TIMEOUT ?? '120000')

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function writePending(id, exec) {
  const args = exec.arguments && typeof exec.arguments === 'object' ? exec.arguments : (exec.args || {})
  const reason = typeof args.description === 'string'
    ? args.description
    : (exec.args === undefined ? '' : JSON.stringify(exec.args).slice(0, 300))
  const agentSession = exec.agent && exec.agent.session ? String(exec.agent.session.id) : null
  mkdirSync(BRIDGE_DIR, { recursive: true })
  writeFileSync(path.join(BRIDGE_DIR, `pending-${id}.json`), JSON.stringify({
    id,
    toolName: exec.name,
    callId: exec.callId ?? null,
    reason,
    agentSession,
    ts: new Date().toISOString(),
  }))
}

async function ask(exec) {
  const id = `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`
  const pendingFile = path.join(BRIDGE_DIR, `pending-${id}.json`)
  const decisionFile = path.join(BRIDGE_DIR, `decision-${id}.json`)
  const cancelFile = path.join(BRIDGE_DIR, `cancel-${id}.json`)
  writePending(id, exec)
  try {
    const deadline = Date.now() + TIMEOUT_MS
    while (Date.now() < deadline) {
      await sleep(POLL_MS)
      if (existsSync(decisionFile)) {
        try {
          const d = JSON.parse(readFileSync(decisionFile, 'utf8'))
          return d.decision === 'allow' ? 'allowed-once' : 'rejected'
        } catch {
          return 'unavailable'
        }
      }
      if (existsSync(cancelFile)) return 'cancelled'
    }
    return 'unavailable' // fail closed
  } finally {
    rmSync(pendingFile, { force: true })
    rmSync(decisionFile, { force: true })
    rmSync(cancelFile, { force: true })
  }
}

export function apply(ctx) {
  ctx.on('tools/pre-execute', async (exec, next) => {
    if (!GATED_TOOLS.has(exec.name)) return next()
    const outcome = await ask(exec)
    if (outcome === 'allowed-once') return next()
    const label = outcome === 'rejected' ? '拒绝' : outcome === 'cancelled' ? '已取消' : '审批不可用（超时）'
    throw new Error(`bash 调用未获批准（${label}，approval bridge）`)
  }, { prepend: true })
}
