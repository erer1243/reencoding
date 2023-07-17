#!/usr/bin/env python3
import sys, os, stat, argparse
from humansize import humansize

parser = argparse.ArgumentParser()
parser.add_argument('divisor', type=int, help='The number of groups to split the files into')
parser.add_argument('index', type=int, help='The index (0-based) of the group to print')
# parser.add_argument('directory', type=str, help='The directory to divide')
args = parser.parse_args()

divisor = args.divisor
index = args.index
# dirname = args.directory

assert index < divisor, "Index must not be greater than divisor"
assert index >= 0, "Index must not be less than zero"
# assert os.path.isdir(dirname), f"Not a directory: {dirname}"

files = []
skipped_any = False
# for basename in os.listdir(dirname):
for path in sys.stdin:
    # path = os.path.join(dirname, basename)
    path = path.strip()
    meta = os.lstat(path)
    if stat.S_ISREG(meta.st_mode):
        files.append((path, meta.st_size))
    else:
        if not skipped_any:
            skipped_any = True
            sys.stderr.write(f"Skipping non-regular files: {path}")
        else:
            sys.stderr.write(f", {path}")
if skipped_any:
    sys.stderr.write("\n")

files.sort(key=lambda p: p[1])
files.reverse()

total_size = 0
for i in range(index, len(files), divisor):
    (path, size) = files[i]
    total_size += size
    print(path)

sys.stderr.write(f"Total size: {humansize(total_size)}\n")
