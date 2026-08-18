#!/usr/bin/env bash
# 伪 Claude Code CLI —— 只用于测试工作台的"通用 CLI 后端"路径，不调用真实 API。
# 用法: fake-claude.sh -p "提示词" --output-format json ...
# 输出 claude --output-format json 形状的 JSON。
set -e
PROMPT=""
prev=""
for a in "$@"; do
  if [ "$prev" = "-p" ]; then PROMPT="$a"; fi
  prev="$a"
done
if [ -z "$PROMPT" ]; then PROMPT="(无提示词)"; fi

cat <<EOF
{"result":[{"type":"text","text":"我是伪 Claude Code（测试后端），收到你的请求：${PROMPT}"}],"session_id":"fake-$(date +%s)","is_error":false}
EOF
