import copy
import json
import os
import shutil
import struct
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from host.model import Capabilities, Profile, compile_profile, fingerprint
from host.sequences import compile_sequence
from host.protocol import Device, INFO, BEGIN_RUNS, RUN, COMMIT, ARM, TRIGGER, ABORT, DISARM, encode
from host.parallel_simulator import ParallelSimulator
from tests.test_sequences import example

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('bits',[10,12,14])
def test_infinite_serialization_memory_and_capability(bits):
    p=replace(example(bits),repeat=0)
    restored=Profile.from_dict(json.loads(json.dumps(p.to_dict())))
    assert fingerprint(restored)==fingerprint(p)
    caps=Capabilities(dac_bits=bits,infinite_repeat=True)
    assert compile_profile(p,caps).duration_ms==compile_profile(replace(p,repeat=1),caps).duration_ms
    assert compile_sequence(p)==compile_sequence(replace(p,repeat=65535))
    with pytest.raises(ValueError,match='新版 FPGA'):compile_profile(p,replace(caps,infinite_repeat=False))
    for invalid in (-1,65536,True):
        with pytest.raises(ValueError):compile_profile(replace(p,repeat=invalid),caps)
    with pytest.raises(ValueError):compile_profile(replace(example(bits,events=True),repeat=0),replace(caps,sequence_steps=64))
    with pytest.raises(ValueError):compile_profile(replace(p,mode='ramp',timing_mode='gate'),caps)
    step=copy.deepcopy(p.sequence_steps);step[0]['repeat']=0
    with pytest.raises(ValueError,match='步骤 1'):compile_profile(replace(p,sequence_steps=step),caps)


def test_old_firmware_rejected_before_upload_starts():
    class OldTransport(ParallelSimulator):
        def __init__(self):super().__init__();self.commands=[]
        def exchange(self,frame):
            self.commands.append(frame[3]);reply=super().exchange(frame)
            if frame[3]==INFO:
                payload=bytearray(reply[6:30]);payload[17]&=~64
                return encode(reply[3],int.from_bytes(reply[4:6],'little'),payload)
            return reply
    sim=OldTransport();dev=Device(sim)
    assert not dev.caps.infinite_repeat
    with pytest.raises(ValueError,match='新版 FPGA'):dev.upload(replace(example(),repeat=0))
    # The preflight refresh must remain read-only even for unsupported features.
    assert sim.commands==[INFO,INFO] and sim.channels[0].pending is None and sim.channels[0].active is None
    dev.upload(example());assert dev.status(0)['valid']


@pytest.mark.parametrize('trigger',['software','external'])
def test_demo_infinite_rounds_abort_and_independent_channels(monkeypatch,trigger):
    now=[1.];monkeypatch.setattr('host.simulator.time.monotonic',lambda:now[0])
    p=replace(example(),repeat=0,trigger=trigger)
    data=compile_sequence(p);gold=[v for v,n in data.runs for _ in range(n)]
    sim=ParallelSimulator();dev=Device(sim)
    for ch in (0,1):dev.upload(replace(p,channel=ch))
    dev.group_command(ARM,[0,1]);assert dev.status(0)['infinite_repeat']
    dev.group_command(TRIGGER,[0,1]) if trigger=='software' else sim.external_trigger(0,'rising')
    for round_no in (0,1,65534,65535,65536,1_000_000):
        for tick in (0,2,len(gold)-1):
            now[0]=1+(round_no*len(gold)+tick+.25)/200_000_000
            s=dev.status(0)
            assert s['state']==2 and s['code']==gold[tick] and s['loops']==min(round_no,65535)
    before=sim.channels[0].started
    if trigger=='external':
        sim.external_trigger(0,'rising');assert dev.status(0)['ignored']==1
        assert sim.channels[0].started==before
    with pytest.raises(RuntimeError):dev.channel_command(DISARM,0)
    dev.channel_command(ABORT,0);now[0]+=1
    sim.external_trigger(0,'rising')
    assert dev.status(0)['state']==0 and dev.status(0)['code']==p.safe
    assert dev.status(1)['state']==2
    dev.channel_command(ARM,0);dev.channel_command(DISARM,0)
    assert dev.status(0)['state']==0 and dev.status(0)['code']==p.initial
    dev.group_command(ABORT,[0,1])


def test_infinite_editor_save_draft_flash_and_channel(tmp_path,monkeypatch):
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication,QMessageBox
    from PySide6.QtTest import QTest
    from host.app import Studio
    from host.storage import Library
    from tests.test_gui import until
    app=QApplication.instance() or QApplication([]);app.setStyle('Fusion')
    errors=[];monkeypatch.setattr(QMessageBox,'warning',lambda *a:errors.append(str(a[-1])))
    lib=Library(tmp_path);w=Studio(lib);w.resize(1540,1120);w.show()
    try:
        w.board_page.dac_count.setValue(4)
        page=w.sequence_page;page.load(example(),False)
        page.repeat.setValue(7);page.repeat_mode.setCurrentIndex(1)
        assert page.collect().repeat==0 and '无限重复' in page.summary.text()
        page.repeat_mode.setCurrentIndex(0);assert page.collect().repeat==7
        page.mode.setCurrentIndex(1);assert page.collect().repeat==1 and not page.repeat_mode.isEnabled()
        page.load(replace(example(),name='无限循环 · 三波形时间表',repeat=0),False)
        page.save();p=next(x for x in lib.list() if x.id==page.profile.id)
        assert p.repeat==0
        w.designer_tabs.setCurrentIndex(1);w.navigate(0);QTest.qWait(150)
        out=ROOT/'build/infinite_repeat';out.mkdir(parents=True,exist_ok=True)
        w.grab().save(str(out/'editor.png'))
        w.connect_button.click();until(app,lambda:w.connected and len(w.channel_statuses)==4)
        w.open_dac(0);cp=w.channel_page;cp.plan.setCurrentIndex(cp.plan.findData(p.id))
        w.board_page.flash_page.capture_boot()
        assert w.board_page.flash_page.boot_profiles['0']['repeat']==0
        cp.buttons['upload'].click();until(app,lambda:not w.busy and cp.buttons['arm'].isEnabled())
        cp.buttons['arm'].click();until(app,lambda:not w.busy and w.channel_statuses[0]['state']==1)
        cp.buttons['external'].click();until(app,lambda:not w.busy and w.channel_statuses[0]['state']==2)
        assert w.channel_statuses[0]['infinite_repeat'] and '无限重复' in cp.status.text()
        QTest.qWait(150);w.grab().save(str(out/'channel.png'))
        cp.buttons['abort'].click();until(app,lambda:not w.busy and w.channel_statuses[0]['state']==0)
        assert w.channel_statuses[0]['code']==p.safe and not errors
    finally:
        if w.connected:w.send('disconnect',None);until(app,lambda:not w.connected and not w.busy)
        w.close();app.processEvents()
    restored=Studio(Library(tmp_path))
    try:assert restored.sequence_page.collect().repeat==0 and restored.sequence_page.repeat_mode.currentData()=='infinite'
    finally:restored.close();app.processEvents()


@pytest.mark.parametrize('bits',[10,12,14])
@pytest.mark.parametrize('external',[0,1])
@pytest.mark.parametrize('runs',[1,3])
def test_rtl_infinite_beyond_counter_wrap(tmp_path,bits,external,runs):
    if not shutil.which('iverilog'):pytest.skip('iverilog required')
    args=['-P',f'tb_infinite.BITS={bits}','-P',f'tb_infinite.EXTERNAL={external}','-P',f'tb_infinite.RUNS={runs}']
    compiled=subprocess.run(['iverilog','-g2012','-s','tb_infinite',*args,'-o',str(tmp_path/'sim'),
        'rtl/common/sample_engine.v','tests/rtl/tb_infinite.v'],capture_output=True,text=True)
    assert compiled.returncode==0,compiled.stderr
    result=subprocess.run(['vvp',str(tmp_path/'sim')],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'PASS infinite' in result.stdout


@pytest.mark.parametrize('bits',[10,12,14])
def test_rtl_boot_and_uart_infinite_timeline(tmp_path,bits):
    from host.board import Board
    from host.boot import generate_boot
    if not shutil.which('iverilog'):pytest.skip('iverilog required')
    p=replace(example(bits),repeat=0);data=compile_sequence(p)
    out=tmp_path/'boot';generate_boot(Board(dac_bits=bits,dac_count=4,boot_profiles={'0':p.to_dict()}),out)
    cfg=1|4|(p.initial<<16)|(p.safe<<32)|(len(data.runs)<<96)
    expected=[v for v,n in data.runs for _ in range(n)]
    (out/'gold.mem').write_text('\n'.join(f'{v:04x}' for v in expected))
    setup=[]
    def add(op,payload=b''):setup.append(f"send(8'h{op:02x},192'h{int.from_bytes(payload,'little'):048x});")
    add(BEGIN_RUNS,struct.pack('<HHHHIBBBBB',p.initial,p.safe,len(data.runs),0,123,1,0,0,0,1))
    for i,(v,n) in enumerate(data.runs):add(RUN,struct.pack('<HHI',i,v,n))
    add(COMMIT);add(ARM)
    (out/'tb.v').write_text(f'''`timescale 1ns/1ps
module tb_boot_infinite;
reg clk=0,rst=1;always #2.5 clk=~clk;reg [255:0] req=0;reg valid=0;
wire [239:0] reply;wire rv;reg [1:0] rise=0,fall=0;wire [{bits-1}:0] code;wire [5:0] monitor;
reg [15:0] gold[0:{len(expected)-1}];integer i,seq=0;
ramp_controller #(.DAC_COUNT(1),.DAC_BITS({bits}),.RUN_ADDR_BITS(11),.LEGACY_ENABLED(0),
 .BOOT_CONFIG(256'h{cfg:064x}),.BOOT_MEMORY_FILE("boot_dac0.mem")) dut(clk,rst,req,valid,reply,rv,1'b1,rise,fall,code,monitor);
task tick;begin @(posedge clk);#0.1;end endtask
task send;input [7:0] op;input [191:0] p;begin
 @(negedge clk);seq=seq+1;req={{16'b0,p,seq[15:0],op,8'd1,16'h5aa5}};valid=1;tick;
 @(negedge clk);valid=0;wait(rv);#0.1;if(reply[55:48]!=0)$fatal(1,"command %h error",op);tick;
end endtask
task play;begin
 wait(dut.fixed_prepared);repeat(5)tick;if(code!={p.initial})$fatal(1,"waiting initial");
 @(negedge clk);rise=1;tick;if(code!==gold[0])$fatal(1,"start");@(negedge clk);rise=0;
 for(i=1;i<{len(expected)*4};i=i+1)begin tick;if(code!==gold[i%{len(expected)}] || monitor[1:0]!=2)$fatal(1,"sample %d",i);end
 send(2,0);if(!reply[236] || reply[63:56]!=2)$fatal(1,"infinite status");
 send(8'h22,0);repeat(5)tick;if(code!={p.safe} || monitor[1:0]!=0)$fatal(1,"abort");
end endtask
initial begin
 $readmemh("gold.mem",gold);repeat(5)tick;@(negedge clk);rst=0;
 send(1,0);if(!reply[190])$fatal(1,"capability");play;
 {''.join(setup)}
 if(dut.active_repeats!=0)$fatal(1,"UART repeat sentinel");play;
 @(negedge clk);rst=1;repeat(5)tick;@(negedge clk);rst=0;repeat(20)tick;
 if(monitor[3] || code!={p.initial})$fatal(1,"reset protection");
 $display("PASS boot/UART infinite {bits}");$finish;
end
initial begin #100000;$fatal(1,"timeout");end
endmodule
''')
    sources=[str(f.resolve()) for f in Path('rtl/common').glob('*.v')]
    r=subprocess.run(['iverilog','-g2012','-s','tb_boot_infinite','-o',str(out/'sim'),*sources,str(out/'tb.v')],capture_output=True,text=True)
    assert r.returncode==0,r.stderr
    r=subprocess.run(['vvp',str(out/'sim')],cwd=out,capture_output=True,text=True,timeout=20)
    assert r.returncode==0,r.stdout+r.stderr
