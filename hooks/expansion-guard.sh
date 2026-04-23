#!/bin/bash

# expansion-guard.sh
# Pre-response guard to suppress option-set expansion for simple imperatives
# Triggers when user's message matches single-action pattern

set -euo pipefail

USER_MESSAGE="${1:-}"

# Guard conditions (ANY match triggers warning)
check_guard() {
  local msg="$1"

  if echo "$msg" | grep -qiE '(just kidding|Just kidding|kidding,)' 2>/dev/null; then
    return 1
  fi

  if [ "$(echo "$msg" | wc -w | tr -d ' ')" -le 8 ]; then
    return 0
  fi

  if echo "$msg" | grep -qiE '^(refresh|refresh |print |print |show |show |just |only |纯 )' 2>/dev/null; then
    local word_after_just
    word_after_just=$(echo "$msg" | grep -ioE '(just|just |only|only )[[:alnum:]]+' | tail -1 | sed 's/^just\s*//;s/^only\s*//')
    case "${word_after_just:-}" in
      kidding|kidding\ *) return 1 ;;
      *) return 0 ;;
    esac
  fi

  if echo "$msg" | grep -qiE '(^|\s)(만|just|only|simply|，纯)(\s|$)' 2>/dev/null; then
    return 0
  fi

  return 1
}

if check_guard "$USER_MESSAGE"; then
  echo ""
  echo "⚠️  [expansion-guard] Simple imperative detected."
  echo "Before responding, verify this is NOT an option-set expansion case:"
  echo "  - Avoid (1)/(2)/(3) or A./B./C. enumerated options"
  echo "  - Avoid multi-choice questions (which/다음 중/선택)"
  echo "  - If user wants one thing, give ONE thing. Do not expand."
  echo ""
  echo "Respond literally or ask one clarifying question only."
  exit 1
fi

exit 0