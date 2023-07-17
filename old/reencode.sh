#!/usr/bin/env bash 
set -eE

# Default options

# H.265 with AAC is good for modern machines
# H.264 is compatible with these settings too, but CRF should be lowered.
# http://trac.ffmpeg.org/wiki/Encode/H.264
# http://trac.ffmpeg.org/wiki/Encode/H.265
CODEC=libx265
ACODEC=aac
PRESET=fast # Presets on libx265 = ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow
CRF=23
RESOLUTION=copy

# Helper functions
probe_stream_elem() {
	ffprobe -loglevel error -select_streams "$1" -show_entries stream="$2" -of csv=p=0:s=, "$3" | sed 's/,//g'
}

probe() {
  PROBEFILE="${2:-"$INFILE"}"
  case "$1" in
  	codec)    probe_stream_elem v:0 codec_name "$PROBEFILE" ;;
  	acodec)   probe_stream_elem a:0 codec_name "$PROBEFILE" ;;
  	height)   probe_stream_elem v:0 height     "$PROBEFILE" ;;
    duration) probe_stream_elem v:0 duration   "$PROBEFILE" ;;
  	isvideo)
  		MIMECATEGORY="$(file -bi "$INFILE" | cut -d '/' -f1)"
  		[ "$MIMECATEGORY" = "video" ] || return 1
  		probe codec "$PROBEFILE" >/dev/null 2>&1 || return 1
  		;;
  	*) err "Unknown probe" "probe $@"
  esac
}

ECHO="$(which echo)"
echo() { "$ECHO" -e "\e[36m[$$]\e[39m$@"; }

context() {
  [ -z "$CONTEXT" ] && {
    [ -n "$INFILE" ] && echo " input = '$INFILE'" >&2 || :
    [ -n "$OUTFILE" ] && echo " output = '$OUTFILE'" >&2 || :
    CONTEXT=yes
  }
}

info() {
  echo "\e[35m[Info]\e[39m $1" >&2
  context
}

warn() {
  echo "\e[33m[$1]\e[39m $2" >&2
  context
}

err() {
  echo "\e[31m[$1]\e[39m $2" >&2
  context
  exit 1
}

FFMPEG="$(which ffmpeg)"
ffmpeg_infile() {
  set -x
  "$FFMPEG" -n -nostdin -hide_banner -i "$INFILE" "$@"
  set +x
}

# Parse arguments
OPTS="$(getopt --options 'o:i:' --longoptions "input:,codec:,acodec:,output:,force,crf:,preset:,replacelink,benchmark,benchnow,replace" -- "$@")"
eval set -- "$OPTS"

while true; do
  case "$1" in
    -i | --input)  INFILE="$2";     shift 2;;
    -o | --output) OUTFILE="$2";    shift 2;;
    --crf)         CRF="$2";        shift 2;;
    --codec)       CODEC="$2";      shift 2;;
    --acodec)      ACODEC="$2";     shift 2;;
    --preset)      PRESET="$2";     shift 2;;
    --force)       FORCE=yes;       shift  ;;
    --replacelink) REPLACELINK=yes; shift  ;;
    --replace)     REPLACE=yes;     shift  ;;
    --benchmark)   BENCHMARK=yes;   shift  ;;
    --benchnow)    BENCHMARK=now;   shift  ;;
    --) shift; break;;
    *) err "Impossible" "Impossible branch in opts" ;;
  esac
done

# Assert input file given
[ -z "$INFILE" ] && err "No input given" "Use -i/--input to pass an input file"

# Fail with extra args
[ "$#" -gt 0 ] && err "Extra arguments" "Unknown extra args: $*"

# Assert not a symlink
[ -L "$INFILE" ] && err "Symlink" "Refusing to follow symlink"

# Assert that INFILE is a video
[ ! -f "$INFILE" ] && err "Not a file" "Not a regular file"
! probe isvideo && err "Not a video" "Not a video file"

# Run benchmark modes
case "$BENCHMARK" in
  yes)
    info "Using builtin benchmark flags for x265. Use --benchnow to control encoding options."
    SCRIPT="$0"
    bench() { "$SCRIPT" -i "$INFILE" --benchnow --codec libx265 --acodec aac --preset "$1" --crf "$2"; }
    set +e
    bench medium 23
    bench medium 30
    bench fast 18
    bench fast 23
    bench fast 30
    set -e
    cat *report
    exit 0
    ;;
  now)
    # 1 minute samples
    BENCHMARKFLAGS="-t 60s"
    BENCHTAG="$CODEC-$ACODEC-$PRESET-$CRF"
    OUTFILE="$BENCHTAG.mp4"
    FORCE=yes

    BENCHBASE="benchmark-base.mp4"

    [ ! -f "$BENCHBASE" ] && {
      warn "Benchmark" "Grabbing benchmark base file"
      ffmpeg_infile     \
        -c:a copy       \
        -c:v copy       \
        $BENCHMARKFLAGS \
        "$BENCHBASE"
    }

    BENCHBASE_SIZE="$(stat -c %s "$BENCHBASE")"
  ;;
esac

# Infer outfile if none was previously generated
[ -z "$OUTFILE" ] && {
  OUTFILE="$(basename "$INFILE")"

  INEXT="${OUTFILE#*.}"
  [ "$INEXT" != "mp4" ] && {
    warn "Not an mp4" "Input file is $INEXT. It will become an mp4. Specify -o/--output to override this."
    OUTFILE="${OUTFILE%.*}".mp4
  }
}

# Setup tmpdir if REPLACE enabled
[ "$REPLACE" = "yes" ] && {
  REPLACETMP="$(mktemp -d)"
  OUTFILE="$REPLACETMP/$OUTFILE"
}

# Assert outfile doesn't already exist 
[ -e "$OUTFILE" ] && err "Already exists" "Will not overwrite existing video file"

# if possible, copy the audio without reencoding
[ "$(probe acodec)" = "$ACODEC" ] && ACODEC=copy

# Skip the entire process if the original file is already encoded the way we want.
[ "$FORCE" != "yes" ] && [ "$BENCHMARK" != "yes" ] && {
  INCODEC="$(probe codec)"
  case "$CODEC" in
    libx265|*hevc*) [ "$INCODEC" = "hevc" ] && ALREADYENCODED=yes ;;
    *264*) [ "$INCODEC" = "h264" ] && ALREADYENCODED=yes ;;
  esac
}

if [ "$ALREADYENCODED" = "yes" ]; then
  if [ "$REPLACE" = "yes" ]; then
    err "Already encoded" "Input file is already encoded as $INCODEC"
  else
    warn "Copied" "Input file is already encoded as $INCODEC"
    set -x
    cp "$INFILE" "$OUTFILE"
    set +x
  fi
else
  onfail() {
    set +x
    rm -f "$OUTFILE"
    err "Conversion failed" "Removing output file"
  }

  # Do it
  trap onfail EXIT
  START_TIME="$(date +%s)"
  ffmpeg_infile       \
    -c:a "$ACODEC"    \
    -c:v "$CODEC"     \
    -crf "$CRF"       \
    -preset "$PRESET" \
    $BENCHMARKFLAGS   \
    "$OUTFILE"
  END_TIME="$(date +%s)"
  trap - EXIT
fi

# Generate benchmark report
[ "$BENCHMARK" = "now" ] && {
  TOTAL_TIME="$(( END_TIME - START_TIME ))"
  OUTSIZE="$(stat -c %s "$OUTFILE")"
  PERCENTSIZE="$(( (OUTSIZE * 100) / BENCHBASE_SIZE ))"
  REPORT="$BENCHTAG-report"
  printf "$BENCHTAG:\t${TOTAL_TIME}s\t${PERCENTSIZE}%%\n" >> "$REPORT"
  exit
}

# Replace files with symlinks, making backups in the input file's dir
[ "$REPLACELINK" = "yes" ] && {
  INDIR="$(dirname "$INFILE")"
  BACKUP="$INDIR/reencoding_backups"
  mkdir -p "$BACKUP"
  mv "$INFILE" "$BACKUP"
  ln -s "$(realpath --relative-to="$INDIR" "$OUTFILE")" "$INFILE"
  exit
}

# Replace file in place if --replace given
[ "$REPLACE" = "yes" ] && {
  INSIZE="$(stat -c %s "$INFILE")"
  OUTSIZE="$(stat -c %s "$INFILE")"
  PERCENTSIZE="$(( (OUTSIZE * 100) / INSIZE ))"
  info "Reencoded video is ${PERCENTSIZE}% the original size."

  # Sanity check on duration of output video
  INDURATION="$(probe duration)"
  OUTDURATION="$(probe duration "$OUTFILE")"
  INSECS="${INDURATION%.*}"
  OUTSECS="${OUTDURATION%.*}"
  DIFF="$(( INSECS - OUTSECS ))"
  ABSDIFF="${DIFF#-}"
  # Allow 2 seconds of unexplained loss
  [ "$ABSDIFF" -gt 2 ] && {
    err "Duration mismatch" "File went from ${INDURATION}s to ${OUTDURATION}s. Refusing to replace."
  }

  # Do the replacement
  cp "$OUTFILE" "$INFILE"
  rm -r "$REPLACETMP"
  exit
}
