"""Isolated Vivado Flash worker; all results and inputs remain in a persistent job folder."""
import argparse,json,locale,subprocess,sys,shutil,hashlib
from pathlib import Path
from .flash import FlashSettings,job_config,bitstream_part,tcl_word
from .runtime import resource_root,prepare_external_runtime


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--job',required=True);args=parser.parse_args()
    jobpath=Path(args.job).resolve();job=json.loads(jobpath.read_text(encoding='utf-8'))
    cfg=FlashSettings.from_dict(job['settings']);operation=job['operation']
    if operation not in ('scan','generate','program','temporary','boot','status'):raise ValueError('未知 Flash 操作')
    out=jobpath.parent;cfg.validate(operation)
    metadata=''
    if operation in ('generate','program','temporary'):
        source=Path(cfg.bitfile).resolve();part=bitstream_part(source)
        snapshot=out/'input.bit';shutil.copy2(source,snapshot);cfg.bitfile=str(snapshot)
        metadata='set cfg(bit_part) '+tcl_word(part)+'\n'
        (out/'input.sha256').write_text(hashlib.sha256(snapshot.read_bytes()).hexdigest(),encoding='ascii')
    config=out/'config.tcl';config.write_text(job_config(cfg,operation,out)+metadata,encoding='utf-8')
    script=out/'flash_studio.tcl';script.write_bytes((resource_root()/'scripts/vivado/flash_studio.tcl').read_bytes())
    vivado=job['vivado']
    if not Path(vivado).is_file():raise ValueError('Vivado 路径不存在')
    for path in (vivado,str(script),str(config),str(out)):
        if any(c in path for c in '%&|<>^!"\r\n'):raise ValueError('Vivado 工作路径不能含 cmd 特殊字符')
    prepare_external_runtime()
    command=[vivado,'-mode','batch','-nojournal','-nolog','-source',str(script),'-tclargs',str(config)]
    result={'operation':operation,'passed':False,'targets':[],'parts':[]}
    child=subprocess.Popen(command,cwd=out,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    success=False
    for raw in child.stdout:
        try:line=raw.decode('utf-8')
        except UnicodeDecodeError:line=raw.decode(locale.getpreferredencoding(False),errors='replace')
        print(line,end='',flush=True)
        if line.startswith('RAMP_TARGET\t'):
            _,target,device=line.strip().split('\t');result['targets'].append({'target':target,'device':device})
        if line.startswith('RAMP_CFGMEM\t'):result['parts'].append(line.strip().split('\t')[1])
        if line.strip()=='RAMP_FLASH_SUCCESS: '+operation:success=True
    rc=child.wait();result['passed']=rc==0 and success;result['parts']=list(dict.fromkeys(result['parts']))
    (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if result['passed'] else 1


if __name__=='__main__':
    try:sys.stdout.reconfigure(encoding='utf-8',errors='replace');sys.exit(main())
    except Exception as exc:print(f'FLASH ERROR: {exc}',flush=True);sys.exit(1)
