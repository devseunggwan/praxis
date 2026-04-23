#!/bin/bash

# expansion-guard.sh
# Pre-response guard to suppress option-set expansion for simple imperatives
# Triggers when user's message matches single-action pattern

set -euo pipefail

USER_MESSAGE="${1:-}"

# Guard conditions (ALL must match to trigger warning)
SHOULD_FLAG=0

# 1. Check message length (≤ 1 sentence)
WORD_COUNT=$(echo "$USER_MESSAGE" | wc -w | tr -d ' ')
if [ "$WORD_COUNT" -le 15 ]; then
  SHOULD_FLAG=1
fi

# 2. Check for single imperative verb + single target pattern
# Simple patterns: "refresh", "print X", "show result", "just do X"
if echo "$USER_MESSAGE" | grep -qiE '^(refresh|refresh |print |print |show |show |just |only |纯 ' 2>/dev/null; then
  SHOULD_FLAG=1
fi

# 3. Check for scope limiter words
if echo "$USER_MESSAGE" | grep -qiE '(만|just|only|simply|，纯|just )' 2>/dev/null; then
  SHOULD_FLAG=1
fi

if [ "$SHOULD_FLAG" -eq 1 ]; then
  echo "⚠️  [expansion-guard] Simple imperative detected."
  echo "Before responding, verify this is NOT an option-set expansion case:"
  echo "  - Avoid (1)/(2)/(3) or A./B./C. enumerated options"
  echo "  - Avoid multi-choice questions (which/다음 중/선택)"
  echo "  - If user wants one thing, give ONE thing. Do not expand."
  echo ""
  echo "Respond literally or ask one clarifying question only."
fi