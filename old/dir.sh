#!/bin/sh
# [ "$#" -ge 3 ] || {
#   echo "usage: $0 DIVISOR INDEX DIRNAME [REENCODE_ARGS...]"
#   exit 1
# }

# DIV="$1"
# IND="$2"
# DNM="$3"
# shift 3

# set -e
# DIR="$(dirname "$0")"
# "$DIR"/split.py "$DIV" "$IND" "$DNM" | xargs -I {} "$DIR"/reencode.sh -i "{}" "$@"

[ "$#" -ge 1 ] || {
  echo "usage: $0 DIRNAME [REENCODE_ARGS...]"
  exit 1
}

INDIR="$1"
shift

set -e
DIR="$(dirname "$0")"

find "$INDIR"     \
  -mindepth 1     \
  -maxdepth 1     \
  -type f         \
  ! -name "*.jpg" \
  ! -name "*.png" \
  | xargs -I {} "$DIR"/reencode.sh -i "{}" "$@"