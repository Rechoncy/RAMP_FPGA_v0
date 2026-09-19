"""Create a full, hashed workspace ZIP and transport parts outside the workspace.

Ignores only the root .git directory; Git history can be exported with git bundle.
Does not obey .gitignore. Never follows symbolic links or directory junctions.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse,hashlib,json,os,time,zipfile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--part-mib',type=int,default=512)
    args=parser.parse_args()
    root=args.root.resolve();out=args.output.resolve()
    if out.is_relative_to(root):parser.error('Output must be outside the workspace')
    if args.part_mib<1:parser.error('Part size must be positive')
    out.mkdir(parents=True,exist_ok=True)
    archive=out/'RAMP_FPGA_v0-full.zip'
    if archive.exists():parser.error('Archive already exists; choose a fresh output directory')
    entries=[];directories=[]
    for current,dirs,files in os.walk(root,followlinks=False):
        current=Path(current)
        if current==root:dirs[:]=[name for name in dirs if name!='.git']
        for name in dirs+files:
            p=current/name
            if p.is_symlink() or p.is_junction():
                raise ValueError(f'Link requires an explicit backup policy: {p}')
        directories.extend((current/name).relative_to(root).as_posix() for name in dirs)
        for name in files:
            p=current/name;s=p.stat()
            entries.append(dict(path=p.relative_to(root).as_posix(),size=s.st_size,mtime_ns=s.st_mtime_ns))
    entries.sort(key=lambda e:e['path'])
    manifest=dict(schema=1,created_utc=datetime.now(timezone.utc).isoformat(),source_root=str(root),
                  excludes=['.git/ (history is in repository.bundle)'],files=entries,directories=sorted(directories))
    last=time.monotonic();done=0
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as z:
        for directory in sorted(directories):z.write(root/directory,directory+'/')
        for index,entry in enumerate(entries,1):
            p=root/entry['path'];info=zipfile.ZipInfo.from_file(p,entry['path'])
            info.compress_type=zipfile.ZIP_STORED if p.suffix.lower() in ('.zip','.gz','.7z','.png','.jpg','.jpeg','.dcp') else zipfile.ZIP_DEFLATED
            info._compresslevel=1
            digest=hashlib.sha256()
            with p.open('rb') as src,z.open(info,'w',force_zip64=True) as dst:
                while block:=src.read(4*1024*1024):digest.update(block);dst.write(block)
            after=p.stat()
            if (after.st_size,after.st_mtime_ns)!=(entry['size'],entry['mtime_ns']):
                raise RuntimeError(f'File changed during backup: {p}')
            entry['sha256']=digest.hexdigest();done+=entry['size']
            if time.monotonic()-last>20:
                print(f'PACK {index}/{len(entries)} files, {done/1e9:.2f} GB read',flush=True);last=time.monotonic()
        z.writestr('FULL_BACKUP_MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8'))
    # A consistent snapshot requires every inventoried file to remain unchanged.
    for entry in entries:
        s=(root/entry['path']).stat()
        if (s.st_size,s.st_mtime_ns)!=(entry['size'],entry['mtime_ns']):
            raise RuntimeError(f'File changed while other files were archived: {entry["path"]}')
    (out/'FULL_BACKUP_MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'PACK COMPLETE: {len(entries)} files; verifying every archived file',flush=True)
    with zipfile.ZipFile(archive) as z:
        for entry in entries:
            with z.open(entry['path']) as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
            if digest!=entry['sha256']:raise RuntimeError(f'Archive verification failed: {entry["path"]}')
    parts=[];whole=hashlib.sha256()
    with archive.open('rb') as src:
        while chunk:=src.read(args.part_mib*1024*1024):
            path=out/f'{archive.name}.{len(parts)+1:03d}';path.write_bytes(chunk);whole.update(chunk)
            parts.append(dict(name=path.name,size=len(chunk),sha256=hashlib.sha256(chunk).hexdigest()))
            print(f'PART {path.name}: {len(chunk)} bytes',flush=True)
    index=dict(schema=1,archive=archive.name,archive_size=archive.stat().st_size,archive_sha256=whole.hexdigest(),
               file_count=len(entries),source_bytes=done,parts=parts,verified_all_files=True,
               created_utc=manifest['created_utc'])
    (out/'BACKUP_INDEX.json').write_text(json.dumps(index,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(index,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
