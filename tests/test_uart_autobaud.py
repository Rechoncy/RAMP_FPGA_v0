from pathlib import Path
import os,shutil,subprocess
import pytest
from host.protocol import encode,FrameParser

ROOT=Path(__file__).resolve().parents[1]


def run_auto(tmp_path,hz,automatic=1,count=9,gap=0):
    if not shutil.which('iverilog'):pytest.skip('Icarus Verilog required')
    for name,command in [('requests',1),('responses',0x81)]:
        frames=[encode(command,0x4320+i,bytes([(i*13+j*7)%256 for j in range(24)])) for i in range(10)]
        (tmp_path/(name+'.hex')).write_text('\n'.join(f'{int.from_bytes(f,"little"):064x}' for f in frames))
    binary=tmp_path/'auto.vvp'
    cmd=['iverilog','-g2012','-s','tb_uart_autobaud',f'-Ptb_uart_autobaud.CORE_HZ={hz}',
         f'-Ptb_uart_autobaud.AUTOBAUD={automatic}',f'-Ptb_uart_autobaud.RATE_COUNT={count}',
         f'-Ptb_uart_autobaud.BYTE_GAP_BITS={gap}','-o',str(binary)]
    cmd += [str(ROOT/name) for name in ('rtl/common/uart.v','rtl/common/uart_autobaud.v','rtl/common/packet_link.v','tests/rtl/tb_uart_autobaud.v')]
    compiled=subprocess.run(cmd,capture_output=True,text=True)
    assert compiled.returncode==0,compiled.stderr
    run=subprocess.run(['vvp',str(binary)],cwd=tmp_path,capture_output=True,text=True,timeout=600)
    assert run.returncode==0,run.stdout+run.stderr
    assert 'PASS auto baud' in run.stdout


@pytest.mark.parametrize('hz',[100_000_000,125_000_000,200_000_000])
def test_same_firmware_switches_all_rates_with_independent_peer(tmp_path,hz):
    run_auto(tmp_path,hz)


def test_new_host_training_is_ignored_by_fixed_uart(tmp_path):
    run_auto(tmp_path,100_000_000,automatic=0,count=1)


@pytest.mark.parametrize('hz,gap',[(100_000_000,1),(125_000_000,1),(200_000_000,4)])
def test_ch340_inter_byte_idle_preserves_training(tmp_path,hz,gap):
    run_auto(tmp_path,hz,count=1,gap=gap)


def test_malformed_training_recovers_and_busy_transmitter_blocks_change(tmp_path):
    if not shutil.which('iverilog'):pytest.skip('Icarus Verilog required')
    binary=tmp_path/'recovery.vvp'
    compile=subprocess.run(['iverilog','-g2012','-s','tb_autobaud_recovery','-o',str(binary),
        str(ROOT/'rtl/common/uart_autobaud.v'),str(ROOT/'tests/rtl/tb_autobaud_recovery.v')],capture_output=True,text=True)
    assert compile.returncode==0,compile.stderr
    run=subprocess.run(['vvp',str(binary)],capture_output=True,text=True,timeout=45)
    assert run.returncode==0,run.stdout+run.stderr
    assert 'PASS autobaud recovery' in run.stdout


def test_uart_retraining_does_not_interrupt_controller_playback(tmp_path):
    if not shutil.which('iverilog'):pytest.skip('Icarus Verilog required')
    requests=[encode(1,i+100) for i in range(2)]
    (tmp_path/'info.hex').write_text('\n'.join(f'{int.from_bytes(f,"little"):064x}' for f in requests))
    words=[(10<<32)|32,(900<<32)|32]+[0]*4094
    (tmp_path/'wave.mem').write_text('\n'.join(f'{w:011x}' for w in words))
    binary=tmp_path/'playback.vvp'
    sources=list((ROOT/'rtl/common').glob('*.v'))
    compiled=subprocess.run(['iverilog','-g2012','-s','tb_autobaud_playback','-o',str(binary),
        *(str(p) for p in sources),str(ROOT/'tests/rtl/tb_autobaud_playback.v')],capture_output=True,text=True)
    assert compiled.returncode==0,compiled.stderr
    run=subprocess.run(['vvp',str(binary)],cwd=tmp_path,capture_output=True,text=True,timeout=180)
    assert run.returncode==0,run.stdout+run.stderr
    assert 'PASS real controller' in run.stdout
    replies=[int(line,16).to_bytes(32,'little') for line in (tmp_path/'responses.hex').read_text().splitlines()]
    assert len(replies)==2
    for request,response in zip(requests,replies):
        assert FrameParser().feed(response)==[response]
        assert response[3]==0x81 and response[4:6]==request[4:6] and response[6]==0
        assert response[25]&16 # INFO payload[19]: automatic UART capability
