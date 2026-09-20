"""Release validation against a relocated EXE with no Python/Qt on PATH."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import pefile

parser=argparse.ArgumentParser()
parser.add_argument('--exe',default='release/Ramp Studio.exe')
parser.add_argument('--flash-bit',help='Optional .bit for packaged Flash image generation; requires --vivado')
parser.add_argument('--vivado',help='Optional real Vivado project/IP generation check')
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]
original=Path(args.exe).resolve()
stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
folder=root/'build'/f'executable_check_{stamp}'
folder.mkdir(parents=True,exist_ok=False)
env={key.upper():value for key,value in os.environ.items()}
for name in ('PYTHONHOME','PYTHONPATH','QT_PLUGIN_PATH','QT_QPA_PLATFORM_PLUGIN_PATH','RAMP_STUDIO_DATA_DIR','RAMP_STUDIO_CHECK_VIVADO','RAMP_STUDIO_CHECK_FLASH_BIT'):
    env.pop(name,None)
system_root=env['SYSTEMROOT']
env['PATH']=os.pathsep.join([str(Path(system_root)/'System32'),system_root])
env['RAMP_STUDIO_CHECK_PORTABLE_DATA']='1'
result={'exe':str(original),'sha256':hashlib.sha256(original.read_bytes()).hexdigest(),'runs':[]}
pe=pefile.PE(str(original))
assert pe.FILE_HEADER.Machine==0x8664 and pe.OPTIONAL_HEADER.Subsystem==2
assert any(entry.id==14 for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries)
pe.close()
result['pe']='Windows x64 GUI executable with embedded icon group'
first=folder/'portable 中文 with spaces'
first.mkdir(); shutil.copy2(original,first/original.name)
for index,location in enumerate((first,folder/'moved location')):
    if index:
        shutil.copytree(first,location)
        assert (location/'user_data'/'migration-marker.txt').read_text()=='retained'
        assert json.loads((location/'user_data'/'board.json').read_text('utf-8'))['dac_bits']==14
    env['QT_QPA_PLATFORM']='offscreen' if index==0 else 'windows'
    report=location/'check.json'
    completed=subprocess.run([str(location/original.name),'--self-test',str(report)],
                             cwd=system_root,env=env,timeout=150)
    check=json.loads(report.read_text('utf-8')) if report.exists() else {'error':'report missing'}
    assert completed.returncode==0 and check.get('passed'),check
    assert Path(check['data_dir'])==location/'user_data'
    assert not Path(check['resource_root']).exists(),'One-file extraction not cleaned after exit'
    (location/'user_data'/'migration-marker.txt').write_text('retained')
    result['runs'].append(check)
    print(f'PASS: {env["QT_QPA_PLATFORM"]}, relocated executable, profiles and 14-bit board',flush=True)
if args.vivado:
    location=folder/'vivado check with spaces'; location.mkdir()
    shutil.copy2(original,location/original.name)
    env['QT_QPA_PLATFORM']='offscreen'
    env['RAMP_STUDIO_CHECK_VIVADO']=str(Path(args.vivado).resolve())
    if args.flash_bit:env['RAMP_STUDIO_CHECK_FLASH_BIT']=str(Path(args.flash_bit).resolve())
    report=location/'check.json'
    completed=subprocess.run([str(location/original.name),'--self-test',str(report)],
                             cwd=system_root,env=env,timeout=300)
    check=json.loads(report.read_text('utf-8')) if report.exists() else {'error':'report missing'}
    assert completed.returncode==0 and check.get('passed'),check
    project=Path(check['project'])
    staged=project.parent.with_name(project.parent.name+'_sources')
    assert (staged/'rtl/platform/xilinx_7series/ramp_top.v').is_file()
    assert (staged/'rtl/common/linear_engine.v').read_bytes()==(root/'rtl/common/linear_engine.v').read_bytes()
    for relative in ('rtl/platform/xilinx_7series/ramp_top_diff.v','rtl/platform/xilinx_7series/clock_reset_adapter.v','rtl/common/scan_engine.v','rtl/common/sample_engine.v','rtl/common/gate_engine.v','rtl/common/controller.v','rtl/common/parallel_controller.v','rtl/common/program_sequencer.v','scripts/vivado/build_studio.tcl','scripts/vivado/refine_timing.tcl','scripts/vivado/validate_reference_pins.tcl'):
        assert (staged/relative).read_bytes()==(root/relative).read_bytes(),f'Bundled source mismatch: {relative}'
    for relative in ('rtl/common/dual_packet_router.v','rtl/common/usb_packet_link.v',
                     'rtl/common/usb_async_fifo.v','rtl/common/ft245_sync.v',
                     'scripts/vivado/usb_cdc.tcl'):
        assert (staged/relative).read_bytes()==(root/relative).read_bytes(),f'Bundled USB source mismatch: {relative}'
    assert '_MEI' not in project.read_text('utf-8')
    result['runs'].append(check)
    check['checks'].append('Bundled gate, sequence, parallel, USB/UART RTL and timing/CDC scripts exactly match verified source')
    print('PASS: packaged GUI -> packaged worker -> real Vivado -> persistent project and RTL',flush=True)
result['passed']=True
report=root/'build'/'validation_executable.json'
report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(f'Validation report: {report}',flush=True)
