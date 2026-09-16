#!/usr/bin/env bash
# Feed source helpers for the sample report tool.
#
# The feed is read line by line from file descriptor 3 so the caller can keep a
# single stream open across several report sections.

read_feed_line() {
  IFS= read -r feed_line <&3 || return 1
  printf '%s\n' "$feed_line"
}

feed_is_ready() {
  [ -r /dev/fd/3 ]
}
