"""Isolated Vivado Flash worker; all results and inputs remain in a persistent job folder."""
import argparse,json,locale,subprocess,sys,shutil,hashlib
from pathlib import Path
from .flash import FlashSettings,job_config,bitstream_part,tcl_word
from .flash_image import inspect_image
from .runtime import resource_root,prepare_external_runtime


def prepare_input(cfg,operation,out):
    """Snapshot the actual input, then inspect that snapshot before any JTAG job."""
    metadata=''
    info=None
    if operation in ('inspect','program') and cfg.image_source=='image':
        source=Path(cfg.image_file).resolve()
        snapshot=out/('input'+source.suffix.lower())
        shutil.copy2(source,snapshot);cfg.image_file=str(snapshot)
        image=inspect_image(snapshot,cfg.size_mib)
        info=image.summary()
        info.update(source=str(source),input_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest())
        expected=out/'firmware.bin';expected.write_bytes(image.data)
        metadata=''.join('set cfg('+k+') '+tcl_word(v)+'\n' for k,v in {
            'program_file':snapshot.as_posix(),'verify_file':expected.as_posix(),
            'image_idcode':info['idcode']}.items())
        (out/'image_info.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
        (out/'input.sha256').write_text(info['input_sha256'],encoding='ascii')
    elif operation in ('generate','program','temporary'):
        source=Path(cfg.bitfile).resolve()
        snapshot=out/'input.bit';shutil.copy2(source,snapshot);cfg.bitfile=str(snapshot)
        part=bitstream_part(snapshot)
        metadata='set cfg(bit_part) '+tcl_word(part)+'\n'
        (out/'input.sha256').write_text(hashlib.sha256(snapshot.read_bytes()).hexdigest(),encoding='ascii')
    return metadata,info


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--job',required=True);args=parser.parse_args()
    jobpath=Path(args.job).resolve();job=json.loads(jobpath.read_text(encoding='utf-8'))
    cfg=FlashSettings.from_dict(job['settings']);operation=job['operation']
    if operation not in ('scan','generate','program','temporary','boot','status','inspect'):raise ValueError('未知 Flash 操作')
    out=jobpath.parent;cfg.validate(operation)
    metadata,info=prepare_input(cfg,operation,out)
    if operation=='inspect':
        result={'operation':operation,'passed':True,'image':info}
        (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(f"镜像检查通过：{info['format']}，{info['bytes']:,} 字节，地址 0，FPGA IDCODE {info['idcode']}。未连接硬件。",flush=True)
        return 0
    config=out/'config.tcl';config.write_text(job_config(cfg,operation,out)+metadata,encoding='utf-8')
    script=out/'flash_studio.tcl';script.write_bytes((resource_root()/'scripts/vivado/flash_studio.tcl').read_bytes())
    vivado=job['vivado']
    if not Path(vivado).is_file():raise ValueError('Vivado 路径不存在')
    for path in (vivado,str(script),str(config),str(out)):
        if any(c in path for c in '%&|<>^!"\r\n'):raise ValueError('Vivado 工作路径不能含 cmd 特殊字符')
    prepare_external_runtime()
    command=[vivado,'-mode','batch','-nojournal','-nolog','-source',str(script),'-tclargs',str(config)]
    result={'operation':operation,'passed':False,'targets':[],'parts':[],'image':info}
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
