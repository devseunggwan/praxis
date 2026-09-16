#!/usr/bin/env bash
# Teardown helpers for the sample report tool.
#
# `exec 3<&-` drops the descriptor the feed was opened on; the shell has no
# other spelling for releasing a descriptor in place.

release_feed() {
  exec 3<&-
}

drop_temp_dir() {
  [ -d "$1" ] && rm -rf "$1"
}
