#!/bin/sh -ev
REENC="$(dirname "$0")"/reencode.py

if [ -S /tmp/xidlehook ]; then
  xidlehook-client --socket /tmp/xidlehook control --action disable
  trap "xidlehook-client --socket /tmp/xidlehook control --action enable" EXIT
fi

find -maxdepth 1 -mindepth 1 -type f | while read f; do
  "$REENC" "$@" "$f"
done