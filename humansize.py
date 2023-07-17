#!/usr/bin/env python3
import os

suffixes = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB']
def humansize(nbytes):
    i = 0
    while nbytes >= 1024 and i < len(suffixes)-1:
        nbytes /= 1024.
        i += 1
    f = f'{nbytes:.2f}'.rstrip('0').rstrip('.')
    return f"{f}{suffixes[i]}"

def humansize_file(path):
    return humansize(os.stat(path).st_size)

if __name__ == "__main__":
	import sys
	print(humansize(int(sys.argv[1])))
