"""RTL checks use actual pin-level FT245 traffic and unrelated core/USB clocks."""
from pathlib import Path
import shutil
import subprocess
import struct

import pytest

from host.protocol import encode

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('flag_delay',[0,9])
def test_ft245_crc_backpressure_and_async_fifo(tmp_path,flag_delay):
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        pytest.skip('Icarus Verilog is not installed')
    requests=[encode(1+(i%2),0x7ff0+i,bytes((i*19+j*7)&255 for j in range(24))) for i in range(12)]
    responses=[encode(0x81+(i%2),0x7ff0+i,bytes((i*23+j*13)&255 for j in range(24))) for i in range(12)]
    for name,frames in [('requests',requests),('responses',responses)]:
        (tmp_path/f'{name}.hex').write_text('\n'.join(f'{int.from_bytes(f,"little"):064x}' for f in frames),encoding='ascii')
    sources=['usb_async_fifo.v','ft245_sync.v','usb_byte_packet_link.v','usb_packet_link.v']
    output=tmp_path/'usb.vvp'
    subprocess.run(['iverilog','-g2012','-s','tb_usb_fifo',f'-Ptb_usb_fifo.FLAG_DELAY={flag_delay}','-o',str(output),
        *[str(ROOT/'rtl/common'/name) for name in sources],str(ROOT/'tests/rtl/tb_usb_fifo.v')],
        check=True,capture_output=True,text=True)
    result=subprocess.run(['vvp',str(output)],cwd=tmp_path,check=True,capture_output=True,text=True,timeout=30)
    assert 'PASS USB:' in result.stdout


def test_usb_async_fifo_wrap_and_reset_while_full(tmp_path):
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        pytest.skip('Icarus Verilog is not installed')
    output=tmp_path/'fifo.vvp'
    subprocess.run(['iverilog','-g2012','-s','tb_usb_async_fifo','-o',str(output),
        str(ROOT/'rtl/common/usb_async_fifo.v'),str(ROOT/'tests/rtl/tb_usb_async_fifo.v')],
        check=True,capture_output=True,text=True)
    result=subprocess.run(['vvp',str(output)],cwd=tmp_path,check=True,capture_output=True,text=True,timeout=10)
    assert 'PASS FIFO:' in result.stdout


def test_usb_real_parallel_controller_batch_commit_and_status(tmp_path):
    from host.protocol import (INFO,SELECT_CHANNEL,BEGIN_RUNS,RUN_BATCH,COMMIT,
                               STATUS,ARM,TRIGGER,ABORT,FrameParser)
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        pytest.skip('Icarus Verilog is not installed')
    identity=0x12785634
    commands=[(INFO,b''),(SELECT_CHANNEL,b'\x02'),
        (BEGIN_RUNS,struct.pack('<HHHHIBBBBB',123,77,51,1,identity,0,2,0,0,1))]
    for start in range(0,51,3):
        commands.append((RUN_BATCH,struct.pack('<HBB',start,3,0)+
                         b''.join(struct.pack('<HI',100+i,1 if i%2 else 256) for i in range(start,start+3))))
    commands.extend((command,b'') for command in (COMMIT,STATUS,ARM,TRIGGER,STATUS,ABORT,STATUS))
    frames=[encode(cmd,index,payload) for index,(cmd,payload) in enumerate(commands)]
    (tmp_path/'parallel_requests.hex').write_text('\n'.join(f'{int.from_bytes(f,"little"):064x}' for f in frames),encoding='ascii')
    output=tmp_path/'parallel.vvp'
    sources=sorted(str(p) for p in (ROOT/'rtl/common').glob('*.v'))
    subprocess.run(['iverilog','-g2012','-s','tb_usb_parallel',f'-Ptb_usb_parallel.COUNT={len(frames)}',
        '-o',str(output),*sources,str(ROOT/'tests/rtl/tb_usb_parallel.v')],
        check=True,capture_output=True,text=True)
    result=subprocess.run(['vvp',str(output)],cwd=tmp_path,check=True,capture_output=True,text=True,timeout=30)
    assert 'PASS USB parallel:' in result.stdout
    raw=b''.join(int(line,16).to_bytes(32,'little') for line in (tmp_path/'parallel_responses.hex').read_text().splitlines())
    replies=FrameParser().feed(raw)
    assert len(replies)==len(frames)
    assert all(reply[3]==request[3]|0x80 and reply[4:6]==request[4:6] and reply[6]==0
               for reply,request in zip(replies,frames))
    assert replies[0][6+19]&6==6  # negotiated USB + RUN_BATCH
    assert replies[0][6+19]&8==0  # no external memory backend
    states=[reply[6:30] for reply,(cmd,_) in zip(replies,commands) if cmd==STATUS]
    assert states[0][1]==0 and struct.unpack_from('<H',states[0],3)[0]==123
    assert states[0][15]==1 and states[0][17]==2
    assert all(struct.unpack_from('<I',p,7)[0]==identity for p in states)
    assert struct.unpack_from('<H',states[-1],3)[0]==77


def test_uart_and_usb_route_replies_switch_and_scope_retry_cache(tmp_path):
    from host.protocol import (INFO,STATUS,SELECT_CHANNEL,BEGIN_RUNS,RUN_BATCH,
                               COMMIT,ARM,ABORT,FrameParser)
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        pytest.skip('Icarus Verilog is not installed')
    identity=0x77123456
    status=encode(STATUS,0x5555)
    usb=[encode(INFO,1),encode(SELECT_CHANNEL,2,b'\0'),
         encode(BEGIN_RUNS,3,struct.pack('<HHHHIBBBBB',123,77,3,1,identity,1,0,0,0,1)),
         encode(RUN_BATCH,4,struct.pack('<HBB',0,3,0)+b''.join(struct.pack('<HI',code,40000) for code in (10,20,30))),
         encode(COMMIT,5),encode(ARM,6),status,status,encode(INFO,0x8888),
         encode(STATUS,0x9992),encode(STATUS,0x9992)]
    uart=[encode(INFO,0x1111),status,status,encode(INFO,0x8888),
          encode(ABORT,0x9991),encode(INFO,0x2222)]
    for name,frames in [('usb',usb),('uart',uart)]:
        (tmp_path/f'{name}_requests.hex').write_text('\n'.join(f'{int.from_bytes(f,"little"):064x}' for f in frames),encoding='ascii')
    output=tmp_path/'dual.vvp'
    subprocess.run(['iverilog','-g2012','-s','tb_dual_transport','-o',str(output),
        *sorted(str(p) for p in (ROOT/'rtl/common').glob('*.v')),str(ROOT/'tests/rtl/tb_dual_transport.v')],
        check=True,capture_output=True,text=True)
    result=subprocess.run(['vvp',str(output)],cwd=tmp_path,check=True,capture_output=True,text=True,timeout=60)
    assert 'PASS dual transport:' in result.stdout
    decoded={}
    for name,frames in [('usb',usb),('uart',uart)]:
        raw=b''.join(int(line,16).to_bytes(32,'little') for line in (tmp_path/f'{name}_responses.hex').read_text().splitlines())
        replies=FrameParser().feed(raw);decoded[name]=replies
        assert len(replies)==len(frames)
        assert all(reply[3]==request[3]|0x80 and reply[4:6]==request[4:6] and reply[6]==0
                   for reply,request in zip(replies,frames))
    code=lambda response:struct.unpack_from('<H',response,9)[0]
    assert code(decoded['uart'][1])==123 and code(decoded['usb'][6])==10
    assert decoded['usb'][6]==decoded['usb'][7]
    assert code(decoded['uart'][2])==20
    assert code(decoded['usb'][9])==77 and decoded['usb'][9]==decoded['usb'][10]


def test_dual_router_queued_requests_reply_backpressure_and_reset(tmp_path):
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        pytest.skip('Icarus Verilog is not installed')
    output=tmp_path/'router.vvp'
    subprocess.run(['iverilog','-g2012','-s','tb_dual_router_backpressure','-o',str(output),
        *sorted(str(p) for p in (ROOT/'rtl/common').glob('*.v')),
        str(ROOT/'tests/rtl/tb_dual_router_backpressure.v')],
        check=True,capture_output=True,text=True)
    result=subprocess.run(['vvp',str(output)],cwd=tmp_path,check=True,capture_output=True,text=True,timeout=10)
    assert 'PASS dual router:' in result.stdout


@pytest.mark.parametrize('dac_bits',[10,12,14])
def test_batch_rtl_rejects_entire_invalid_request_before_writing(tmp_path,dac_bits):
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        pytest.skip('Icarus Verilog is not installed')
    output=tmp_path/'batch.vvp'
    subprocess.run(['iverilog','-g2012','-s','tb_run_batch_atomic',f'-Ptb_run_batch_atomic.DAC_BITS={dac_bits}','-o',str(output),
        *sorted(str(p) for p in (ROOT/'rtl/common').glob('*.v')),
        str(ROOT/'tests/rtl/tb_run_batch_atomic.v')],
        check=True,capture_output=True,text=True)
    result=subprocess.run(['vvp',str(output)],cwd=tmp_path,check=True,capture_output=True,text=True,timeout=10)
    assert 'PASS RUN_BATCH atomic validation:' in result.stdout


@pytest.mark.parametrize('flag_delay',[0,9])
def test_registered_ft245_reserves_full_boundary_and_keeps_continuous_reads(tmp_path,flag_delay):
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        pytest.skip('Icarus Verilog is not installed')
    output=tmp_path/'registered.vvp'
    subprocess.run(['iverilog','-g2012','-s','tb_ft245_registered',
        f'-Ptb_ft245_registered.FLAG_DELAY={flag_delay}','-o',str(output),
        str(ROOT/'rtl/common/ft245_sync.v'),str(ROOT/'rtl/common/usb_async_fifo.v'),
        str(ROOT/'tests/rtl/tb_ft245_registered.v')],check=True,capture_output=True,text=True)
    result=subprocess.run(['vvp',str(output)],cwd=tmp_path,check=True,capture_output=True,text=True,timeout=15)
    assert 'PASS registered FT245:' in result.stdout
