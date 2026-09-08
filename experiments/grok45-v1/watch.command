#!/bin/sh
set -eu
watch_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec /usr/bin/python3 "$watch_dir/watch.py"
