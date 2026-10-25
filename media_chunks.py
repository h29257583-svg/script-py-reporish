"""Order recoverable clips and remove byte-identical repetitions before merging."""
import hashlib
from pathlib import Path
import re


def chunk_order(path):
    match=re.search(r'_chunk(\d+)(?:_short_exit_(\d+))?\.ts',str(path))
    if not match:return (0,0,str(path))
    return (int(match[1]),int(match[2]) if match[2] else 1_000_000,str(path))


def unique_chunk_paths(paths):
    seen={};inode_hashes={};unique=[];duplicates=[]
    for value in sorted(paths,key=chunk_order):
        path=Path(value);stat=path.stat();identity=(stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns)
        digest=inode_hashes.get(identity)
        if digest is None:
            hasher=hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda:stream.read(1024*1024),b''):hasher.update(block)
            digest=hasher.hexdigest();inode_hashes[identity]=digest
        key=(stat.st_size,digest)
        if key in seen:duplicates.append(value)
        else:seen[key]=value;unique.append(value)
    return unique,duplicates
