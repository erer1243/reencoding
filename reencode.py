#!/usr/bin/env python3
import sys
import os
import functools
import time
import argparse
import subprocess
import shlex
import shutil
import tempfile
import filecmp
import pathlib
import json
import humansize
import sqlite3
import hashlib
import traceback
from os import path
from dataclasses import dataclass

class Log:
    cyan    = '\033[36m'
    magenta = '\033[35m'
    yellow  = '\033[33m'
    red     = '\033[31m'
    green   = '\033[92m'
    reset   = '\033[0m'
    pidtag  = f"{cyan}[{os.getpid()}]{reset}"
    tracing = False

    def print(first, *args): 
        print(f"{Log.pidtag}{first}", *args, file=sys.stderr)
        sys.stderr.flush()

    def traced(func):
        def wrapper(*args, **kwargs):
            printed_args = ", ".join(map(repr, args))
            printed_kwargs = ", ".join(map(lambda i: f"{str(i[0])}={repr(i[1])}", kwargs.items()))
            printed_all_args = ", ".join([a for a in [printed_args, printed_kwargs] if len(a) != 0])
            Log.trace(f"{func.__qualname__}({printed_all_args})")
            return func(*args, **kwargs)
        return wrapper
        
    def trace(*args):
        if Log.tracing:
            Log.print(f"{Log.magenta}[TRACE]{Log.reset}", *args)

    def info(*args):
        Log.print(f"{Log.green}[INFO]{Log.reset}", *args)
    
    def warn(*args): 
        Log.print(f"{Log.yellow}[WARN]{Log.reset}", *args)

    class ErrorCalled(Exception): pass
    def error(*args, exception=True):
        Log.print(f"{Log.red}[ERROR]{Log.reset}", *args)
        if exception:
            raise Log.ErrorCalled()

    def check(cond, *args):
        if not cond:
            Log.error(*args)

class Probe:
    @functools.cache
    @Log.traced
    def probe(file):
        result = run([
            "ffprobe",
            "-loglevel", "error", 
            "-show_entries", "stream=codec_name,codec_type:format=duration", 
            "-print_format", "json", 
            file
        ])
        data = json.loads(result)
        video_codecs = [s["codec_name"] for s in data["streams"] if s["codec_type"] == "video"]
        audio_codecs = [s["codec_name"] for s in data["streams"] if s["codec_type"] == "audio"]
        Log.check(len(video_codecs) == 1, "No video stream")
        Log.check(len(audio_codecs) <= 1, "More than 1 audio stream")
        Log.check("format" in data, "Bad format data")
        Log.check("duration" in data["format"], "Bad format data")
        video_codec = video_codecs[0]
        audio_codec = audio_codecs[0] if len(audio_codecs) == 1 else None
        duration = float(data["format"]["duration"])
        return (video_codec, audio_codec, duration)

    def codec(file):
        return Probe.probe(file)[0]

    def acodec(file):
        return Probe.probe(file)[1]

    def duration(file):
        return Probe.probe(file)[2]

    def isvideo(file):
        try:
            Probe.probe(file)
            return True
        except Log.ErrorCalled:
            return False

@Log.traced
def run(cmd_list):
    Log.info(shlex.join(cmd_list))
    proc = subprocess.run(cmd_list, check=True, stdout=subprocess.PIPE)
    result = proc.stdout.decode('utf-8') 
    return result

@Log.traced
def ffmpeg(args: list[str]):
    return run([
        "ffmpeg",
        "-n",
        "-nostdin",
        "-hide_banner"
    ] + args)

def as_mp4(f: str):
    dirname = path.dirname(f)
    basename = path.basename(f)
    mp4 = basename.rsplit(".", 1)[0] + ".mp4"
    return path.join(dirname, mp4)

def file_size(f: str):
    return os.stat(f).st_size

def file_size_percent(f1: str, f2: str):
    s1 = os.stat(f1).st_size
    s2 = os.stat(f2).st_size
    pct = 100 * s1 / s2
    return round(pct, 1)

def files_on_same_fs(f1: str, f2: str):
    d1 = os.stat(f1).st_dev
    d2 = os.stat(f2).st_dev
    return d1 == d2

@Log.traced
def copy_file(src: str, dst: str):
    if path.exists(dst):
        Log.error(f"Refusing to overwrite '{src}' with '{dst}'")

    test_file = pathlib.Path(dst)
    test_file.touch()
    can_link = files_on_same_fs(src, dst)
    test_file.unlink()

    if can_link:
        Log.info(f"Hardlinking '{src}' to '{dst}'")
        os.link(src, dst)
    else:
        Log.info(f"Copying '{src}' to '{dst}'")
        shutil.copy2(src, dst)
    

class BadEncodingDatabase:
    def __init__(self):
        script_dir = path.dirname(__file__) 
        db_path = path.join(script_dir, "badencodings.db")
        self.db = sqlite3.connect(db_path)
        self.init_db()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.commit()
        self.db.close()

    @Log.traced
    def init_db(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS bad_encodings(
                hash BLOB,
                crf INT,
                preset TEXT,
                output_bytes INT,
                PRIMARY KEY (hash, crf, preset)
            )
        """)
        self.db.commit()
    
    @Log.traced
    def check(self, f: str, crf: int, preset: str):
        params = (BadEncodingDatabase.hash_file(f), crf, preset)
        cursor = self.db.execute("""
            SELECT output_bytes FROM bad_encodings WHERE hash == ? AND crf == ? AND preset == ?
        """, params)
        res = cursor.fetchone()
        cursor.close()
        if res is not None: res = res[0]
        return res

    @Log.traced
    def insert(self, f: str, crf: int, preset: str, output_bytes: int):
        params = (BadEncodingDatabase.hash_file(f), crf, preset, output_bytes)
        cursor = self.db.execute("""
            INSERT INTO bad_encodings (hash, crf, preset, output_bytes) VALUES (?, ?, ?, ?)
        """, params)
        self.db.commit()
        cursor.close()

    @functools.cache
    @Log.traced
    def hash_file(p: str):
        Log.info("Hashing input file")
        hasher = hashlib.sha1()
        with open(p, "rb") as f:
            while buf := f.read(1024 * 1024):
                hasher.update(buf)
        return hasher.digest()

# @dataclass
# class EncodingParameters:
#     crf: int
#     preset: str
#     force: bool
#     extra_args: list[str] = []
#     dont_copy: bool = False

# The big one
@Log.traced
def reencode(in_file: str, out_file: str, crf: int, preset: str, force: bool, extra_args=[], dont_copy=False):
    if not out_file.endswith(".mp4"):
        Log.warn("File will be converted into mp4")
        out_file = as_mp4(out_file)

    Log.info("input  =", "'" + in_file + "'")
    Log.info("output =", "'" + out_file + "'")
    Log.check(path.isfile(in_file),      "Not a file")
    Log.check(not path.islink(in_file),  "Refusing symlink")
    Log.check(not path.exists(out_file), "Output file already exists")
    Log.check(Probe.isvideo(in_file),    "Not a video file")
    codec = Probe.codec(in_file)
    acodec = Probe.acodec(in_file)
    encoder = (codec == "hevc") and "copy" or "libx265"
    aencoder = (acodec == "aac" or acodec is None) and "copy" or "aac"
    
    if not force and encoder == "copy" and aencoder == "copy":
        Log.warn("Input file is already encoded as hevc/aac")
        if dont_copy:
            return None
        else:
            copy_file(in_file, out_file)
    else:
        in_size = humansize.humansize_file(in_file)

        with BadEncodingDatabase() as db, tempfile.TemporaryDirectory() as tmp_dir:
            Log.trace("tmp_dir =", tmp_dir)
            
            if not force and (prev_result := db.check(in_file, crf, preset)):
                out_size = humansize.humansize(prev_result)
                Log.warn(f"File in bad encodings database (increases {in_size} -> {out_size})")
                if dont_copy:
                    return None
                else:
                    copy_file(in_file, out_file)
                    return out_file

            temp_out_file = path.join(tmp_dir, path.basename(out_file))        
            ffmpeg(extra_args + [
                "-i", in_file, 
                "-c:a", aencoder,
                "-c:v", encoder, 
                "-crf", str(crf), 
                "-preset", preset,
                temp_out_file
            ])

            percent = file_size_percent(temp_out_file, in_file)
            out_size = humansize.humansize_file(temp_out_file)
            Log.info(f"Output is {percent}% the original size ({in_size} -> {out_size})")

            if not force and percent >= 100:
                out_bytes = file_size(temp_out_file)
                db.insert(in_file, crf, preset, out_bytes)
                Log.error("File size increased!")

            copy_file(temp_out_file, out_file)

    return out_file

BENCHMARKS = [
    (28, "medium"),
    (28, "fast"),
    (25, "fast"),
    (23, "fast")
]
BENCHMARK_DURATION = 60 # seconds
@Log.traced
def benchmark(in_file: str, out_dir: str):
    # If possible, grab 1 minute in the middle of the original
    # Otherwise do the whole video
    dur = Probe.duration(in_file)
    if dur > 60:
        skiptime = int((dur - BENCHMARK_DURATION) / 2)
        extra_args = ["-t", str(BENCHMARK_DURATION), "-ss", str(skiptime)]
    else:
        extra_args = []
   
    # Grab sample file
    sample = path.join(out_dir, "sample.mp4")
    ffmpeg(extra_args + [
        "-i", in_file,
        "-c:a", "copy",
        "-c:v", "copy",
        sample
    ])
    
    # Do benchmarks
    reports = []
    for (crf, preset) in BENCHMARKS:
        out_file = path.join(out_dir, f"{preset}-{crf}.mp4")
        start = time.time()
        reencode(in_file, out_file, crf, preset, True, extra_args=extra_args)
        reencode_time = round(time.time() - start)
        percent = file_size_percent(out_file, sample)
        reports.append(f"{out_file}\t{reencode_time}s\t{percent}%")

    # Write reports
    report = path.join(out_dir, "report")
    with open(report, "w") as f:
        for r in reports:
            Log.info(r)
            f.write(r)
            f.write("\n")

@Log.traced
def backup(f: str):
    shutil.move(f, "REENC_BACKUP-" + f)

def is_backup(f: str):
    return "REENC_BACKUP" in f

@dataclass
class RunArgs:
    in_file: str
    crf: int
    preset: str
    force: bool
    replacelink: bool
    nobackup: bool
    probe: bool
    outdir: str
    trace: bool
    replace: bool
    # outfile: str
    benchmark: bool

@Log.traced
def reencode_replace(ra: RunArgs):
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Passing dont_copy=True means out_file is None when reencoding isn't performed
        out_file = path.join(tmp_dir, path.basename(ra.in_file))

        if ra.in_file.lower().endswith("mp4"):
            dest = ra.in_file
        else:
            dest = as_mp4(ra.in_file)

        if dest != ra.in_file and path.exists(dest):
            Log.error(f"Replacing '{ra.in_file}' would overwrite a different file: '{dest}'")

        if out_file := reencode(ra.in_file, out_file, ra.crf, ra.preset, ra.force, dont_copy=True):
            if ra.nobackup:
                os.remove(ra.in_file)
            else:
                backup(ra.in_file)

            copy_file(out_file, dest)

@Log.traced
def print_probe(f: str):
    dur = Probe.duration(in_file)
    min = int(dur / 60)
    sec = round(dur % 60, 2)
    size = humansize.humansize_file(in_file)
    codec = Probe.codec(in_file)
    print(size, codec, f"{min}:{sec:02}", f, sep=", ")

def skip_file(f: str):
    SKIP_EXTS = {"jpg", "png", "jpeg", "posts", "yml", "info", "sh", "pdf", "swf", "xml", "mp3", "css", "url", 
                 "txt", "html", "exe", "py", "dv", "heic", "db", "zip", "psd", "pyc", "pem", "jpe", "typed", "readme", 
                 "md", "rar"}
    has_skip_ext = any(map(lambda ext, s=f: s.lower().endswith(ext), SKIP_EXTS))
    return is_backup(f) or has_skip_ext

def main_run(ra: RunArgs):
    Log.tracing = ra.trace

    if skip_file(ra.in_file):
        Log.error("Skipped", ra.in_file)
    elif ra.probe:
        print_probe(ra.in_file)
    elif ra.benchmark:
        benchmark(ra.in_file, ra.outdir)
    elif ra.replace:
        reencode_replace(ra)
    else:
        out_file = path.join(ra.outdir, path.basename(ra.in_file))
        out_file = reencode(ra.in_file, out_file, ra.crf, ra.preset, ra.force)

        if ra.replacelink:
            if ra.nobackup:
                os.remove(ra.in_file)
            else:
                backup(ra.in_file)
            target = path.relpath(out_file, path.dirname(ra.in_file))
            os.symlink(target, ra.in_file)

def main():
    PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
    # INFER_OUTFILE = "<INPUT>.mp4"

    parser = argparse.ArgumentParser(description="ffmpeg wrapper", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("INPUT", help="Input video file")
    parser.add_argument("--preset", help="libx265 preset", default="fast", choices=PRESETS)
    parser.add_argument("--crf", help="libx265 CRF value, 0-51", type=int, default=23)
    parser.add_argument("--force", help="Reencode videos even if they are already encoded as HEVC", action='store_true')
    parser.add_argument("--replacelink", help="Replace input file with a symlink to the output file", action="store_true")
    parser.add_argument("--nobackup", help="Disable backing up original files to a reencoding_backups folder", action="store_true")
    parser.add_argument("--probe", help="Print probe information for input file (hint: pipe loops into `column -t -s , -l 4`)", action="store_true")
    parser.add_argument("--outdir", help="Output reencoded video into the given directory, with an inferred name", default="./")
    parser.add_argument("--trace", help="Enable tracing logs", action="store_true")
    output_args = parser.add_mutually_exclusive_group()
    output_args.add_argument("--replace", help="Replace the input file after encoding", action="store_true")
    # output_args.add_argument("--outfile", help="Output reencoded video to the given path, must end in .mp4", default=INFER_OUTFILE)
    output_args.add_argument("--benchmark", help="Run benchmarks", action="store_true")
    args = parser.parse_args()

    ra = RunArgs(args.INPUT, args.crf, args.preset, args.force, args.replacelink, args.nobackup, args.probe, args.outdir, args.trace, args.replace, args.benchmark)
    main_run(ra)

if __name__ == "__main__":
    exitcode = 0
    try:
        main()
    except (subprocess.CalledProcessError, Log.ErrorCalled):
        exitcode = 0
    except KeyboardInterrupt:
        print(file=sys.stderr)
        Log.warn("KeyboardInterrupt")
        exitcode = 1
    except Exception as e:
        # import pdb
        extype, value, tb = sys.exc_info()
        traceback.print_exc()
        # pdb.post_mortem(tb)
        exitcode = 1
    finally:
        print()
        sys.exit(exitcode)
