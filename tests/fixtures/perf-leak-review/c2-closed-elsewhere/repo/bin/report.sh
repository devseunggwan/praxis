#!/usr/bin/env bash
# Print the first lines of the configured feed.
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=../lib/source.sh
. "$here/lib/source.sh"
# shellcheck source=../lib/teardown.sh
. "$here/lib/teardown.sh"

main() {
  open_feed "$1"
  while read_feed_line; do
    :
  done
  release_feed
}

main "$@"
