import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
import pytest
from host.board import Board,generate_xdc
from host.boot import generate_boot,identity,matches_boot,snapshot_assignments
from host.functions import compile_samples
from host.model import Profile,Capabilities
from host.protocol import Device,ARM,ABORT,DISARM
from host.simulator import Simulator
from test_rtl import run_tb

ROOT=Path(__file__).resolve().parents[1]


def test_fixed_external_rearm_rtl():
    run_tb('tb_auto_rearm',['rtl/common/sample_engine.v'])


def test_simulator_rearms_each_repeat_batch(monkeypatch):
    now=[0.];monkeypatch.setattr('host.simulator.time.monotonic',lambda:now[0])
    p=Profile(mode='ramp',repeat=3,rise_ms=.001,hold_ms=.001,fall_ms=.001)
    sim=Simulator();dev=Device(sim);dev.upload(p);dev.command(ARM)
    assert dev.caps.fixed_auto_rearm
    for n in range(1,4):
        sim.external_trigger();sim.external_trigger();now[0]+=.001
        s=dev.status();assert s['state']==1 and s['loops']==n*3 and s['code']==p.initial and s['ignored']==n
    dev.command(DISARM);sim.external_trigger();assert dev.status()['state']==0
    dev.command(ARM);sim.external_trigger();dev.command(ABORT);now[0]+=1
    assert dev.status()['state']==0 and dev.status()['code']==p.safe


@pytest.mark.parametrize('bits',[10,12,14])
def test_boot_bram_four_channels_without_host_and_reset_guard(tmp_path,bits):
    if not shutil.which('iverilog'):pytest.skip('iverilog unavailable')
    mid=1<<(bits-1)
    p=Profile(mode='ramp',dac_bits=bits,initial=mid,safe=mid+12,amplitude=mid-6,
              rise_ms=.000015,hold_ms=.00001,fall_ms=.000015,repeat=2)
    gate=replace(p,channel=1,timing_mode='gate',repeat=1)
    software=replace(p,channel=2,trigger='software')
    b=Board(dac_count=4,dac_bits=bits,boot_profiles={str(q.channel):q.to_dict() for q in [p,gate,software]})
    out=tmp_path/'boot';manifest=generate_boot(b,out)
    wrapper=(out/'ramp_boot_top.v').read_text()
    configs=wrapper.split(".BOOT_CONFIGS(2048'h")[1].split(')')[0]
    files=wrapper.split(".BOOT_FILES(16384'h")[1].split(')')[0]
    values=[code for code,count in compile_samples(p).runs for _ in range(count)]*2
    checks='\n'.join(f'if(codes[0+:{bits}]!={bits}\'d{code}) $fatal(1,"boot sample");tick;' for code in values)
    tb=f'''`timescale 1ns/1ps
module tb_boot;
reg clk=0;always #2.5 clk=~clk;reg rst=1;reg [1:0] rise=0,fall=0;
reg valid=0;reg [255:0] req=0;wire [239:0] reply;wire reply_valid;wire [{bits*4-1}:0] codes;
parallel_controller #(.DAC_BITS({bits}),.BOOT_CONFIGS(2048'h{configs}),.BOOT_FILES(16384'h{files})) dut(
clk,rst,req,valid,reply,reply_valid,1'b1,rise,fall,codes);
task tick;begin @(negedge clk);end endtask
task command;input [7:0] cmd;input [15:0] seq;input [191:0] payload;
begin tick;req={{16'b0,payload,seq,cmd,8'd1,16'h5aa5}};valid=1;tick;valid=0;wait(reply_valid);#0.1;
if(reply[55:48]!=0) $fatal(1,"command rejected");tick;end endtask
integer event_no;
initial begin
repeat(6) tick;rst=0;repeat(20) tick;
if(dut.monitor[0][3:0]!=4'b1101 || dut.monitor[1][1:0]!=1 || dut.monitor[2][1:0]!=1 || dut.monitor[3][3]!=0) $fatal(1,"boot arm/valid");
command(1,1,0);if(reply[191:184]!=255) $fatal(1,"boot caps");
command(2,2,0);if(!reply[238] || reply[135:104]!=32'd{identity(p)}) $fatal(1,"boot identity");
for(event_no=1;event_no<=3;event_no=event_no+1) begin
tick;rise=1;tick;rise=0;
{checks}
if(codes[0+:{bits}]!={mid} || dut.monitor[0][1:0]!=1) $fatal(1,"boot rearm");
repeat(20) tick;
if(codes[{bits}+:{bits}]!={mid-6}) $fatal(1,"gate did not hold");
tick;fall=1;tick;fall=0;repeat(20) tick;
if(codes[{bits}+:{bits}]!={mid} || dut.monitor[1][1:0]!=1) $fatal(1,"gate did not rearm");
if(codes[{bits*2}+:{bits}]!={mid} || codes[{bits*3}+:{bits}]!={mid}) $fatal(1,"untriggered channel moved");
end
command(2,3,0);if(reply[103:88]!=6) $fatal(1,"repeat batch accounting");
// UART replace must clear boot status and preserve normal upload semantics.
command('h23,4,0);
command('h30,5,192'h000000000000000101000001123456780001000103000200);
command('h31,6,192'h000000000000000000000000000000000000000401110000);
command('h12,7,0);command(2,8,0);if(reply[238]) $fatal(1,"upload kept boot flag");
// RAM is not reinitialized by clock reset: never re-arm stale boot metadata.
rst=1;repeat(6) tick;rst=0;repeat(20) tick;
if(dut.monitor[0][3] || dut.monitor[1][3] || dut.monitor[2][3]) $fatal(1,"reset restored stale config");
$display("PASS initialized RAM, boot arm, repeated external, gate, software idle, unused DAC, UART replacement, reset guard");$finish;
end
initial begin #200000;$fatal(1,"timeout");end
endmodule
'''
    (out/'tb.v').write_text(tb)
    sources=[ROOT/'rtl/common'/f for f in ['parallel_controller.v','controller.v','linear_engine.v','scan_engine.v','sample_engine.v','gate_engine.v','sequence_engine.v','program_sequencer.v']]
    subprocess.run(['iverilog','-g2012','-s','tb_boot','-o',str(out/'sim'),*map(str,sources),str(out/'tb.v')],check=True,capture_output=True,text=True)
    r=subprocess.run(['vvp',str(out/'sim')],cwd=out,check=True,capture_output=True,text=True)
    assert 'PASS' in r.stdout
    assert manifest[0]['runs']==len(compile_samples(p).runs)
    assert matches_boot(p,dict(valid=True,boot_profile=True,config_id=identity(p)),Capabilities(boot_profiles=True))
    assert not matches_boot(replace(p,amplitude=mid-7),dict(valid=True,boot_profile=True,config_id=identity(p)),Capabilities(boot_profiles=True))


def test_boot_snapshot_validation_and_roundtrip(tmp_path):
    from host.storage import Library
    lib=Library(tmp_path/'data');p=Profile(mode='ramp');lib.save(p)
    (lib.root/'parallel.json').write_text(json.dumps({'0':dict(profile=p.id,enabled=True),'1':dict(profile=p.id,enabled=False)}))
    snap=snapshot_assignments(lib,4);assert list(snap)==['0']
    b=Board(dac_count=4,boot_profiles=snap);assert Board.from_dict(b.to_dict()).boot_profiles==snap
    assert 'get_pins core/u_clock/u_ip/inst/mmcm_adv_inst/CLKOUT0' in generate_xdc(b)
    p.channel=4;b.boot_profiles={'4':p.to_dict()}
    with pytest.raises(ValueError,match='编号'):generate_boot(b,tmp_path/'wrong')
    p.channel=0;p.mode='function';p.function_layout='single';p.function='1024'
    b.boot_profiles={'0':p.to_dict()}
    with pytest.raises(ValueError,match='原码'):generate_boot(b,tmp_path/'range')


def test_boot_gui_snapshot_saved_version_and_channel_recognition(tmp_path,monkeypatch):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication,QMessageBox
    from host.app import Studio
    from host.storage import Library
    from host.model import fingerprint
    app=QApplication.instance() or QApplication([]);errors=[]
    monkeypatch.setattr(QMessageBox,'warning',lambda *a:errors.append(a[-1]))
    lib=Library(tmp_path);p=Profile(mode='ramp',initial=300,amplitude=290);lib.save(p)
    w=Studio(lib);w.resize(1450,1100);w.show();app.processEvents()
    try:
        pool=w.parallel_page;pool.rows[0]['combo'].setCurrentIndex(pool.rows[0]['combo'].findData(p.id))
        flash=w.board_page.flash_page;flash.capture_boot();assert not errors,errors
        assert flash.boot_profiles['0']['initial']==300 and not flash.running()
        assert json.loads((tmp_path/'board.json').read_text('utf-8'))['boot_profiles']['0']==p.to_dict()
        # Library changes do not silently change an already captured boot snapshot.
        p.amplitude=280;lib.save(p);assert flash.boot_profiles['0']['amplitude']==290
        p.amplitude=290;lib.save(p);pool.refresh_library()
        w.caps=replace(w.caps,parallel_channels=True,boot_profiles=True,fixed_auto_rearm=True)
        w.connected=True;w.channel_statuses={0:dict(channel=0,state=1,valid=True,config_id=identity(p),code=300,loops=0,boot_profile=True)}
        w.open_dac(0);w.channel_page.refresh()
        assert '无需重新下载' in w.channel_page.version.text()
        w.navigate(1);w.board_page.tabs.setCurrentWidget(flash);app.processEvents()
        from PySide6.QtTest import QTest
        QTest.qWait(300)
        w.grab().save(str(ROOT/'build/boot_flash_page.png'))
        w.connected=False;flash.clear_boot();assert not w.board_page.collect().boot_profiles
    finally:
        w.connected=False;w.saved_fingerprint=fingerprint(w.profile);w.close();app.processEvents()
