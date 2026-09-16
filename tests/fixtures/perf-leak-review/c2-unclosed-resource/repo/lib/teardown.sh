#!/usr/bin/env bash
# Teardown helpers for the sample report tool.

drop_temp_dir() {
  [ -d "$1" ] && rm -rf "$1"
}
