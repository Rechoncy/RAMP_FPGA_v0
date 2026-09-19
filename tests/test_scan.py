import json,struct,subprocess,shutil,copy
from pathlib import Path
from dataclasses import replace
import pytest
from host.model import Profile,Capabilities,compile_profile,fingerprint
from host.scan import SCAN_FORMAT,preview_scan
from host.protocol import Device,ARM,TRIGGER,ABORT,DISARM,BEGIN_SCAN,COMMIT,STATUS,GROUP_COMMAND,SELECT_CHANNEL,encode
from host.parallel_simulator import ParallelSimulator


def plan(bits=10,**kwargs):
    return replace(Profile(mode='scan',name='触发步进测试',dac_bits=bits,initial=1<<(bits-1),safe=(1<<(bits-1))+1,
        scan_start=(1<<bits)-1,scan_step=-7,scan_points=4),**kwargs)


def packet(p,identity=123):
    return struct.pack(SCAN_FORMAT,p.initial,p.safe,p.scan_start,p.scan_step,p.scan_points,identity,p.channel,p.trigger_input,
        int(p.trigger=='external') | (int(p.trigger_edge=='falling')<<1) | (int(p.scan_end=='loop')<<2))


@pytest.mark.parametrize('bits',[10,12,14])
def test_scan_validation_storage_and_no_sample_memory(bits):
    from host.functions import compile_samples
    p=plan(bits);caps=Capabilities(dac_bits=bits,trigger_scan=True)
    assert compile_profile(p,caps).duration_ms==0 and compile_samples(p).runs==()
    assert fingerprint(Profile.from_dict(json.loads(json.dumps(p.to_dict()))))==fingerprint(p)
    assert not any(k.startswith('scan_') for k in Profile(mode='ramp').to_dict())
    assert preview_scan(p)[1]==[p.initial,p.initial,p.scan_start,p.scan_start,p.scan_start-7,p.scan_start-7,p.scan_start-14,p.scan_start-14,p.scan_start-21,p.scan_start-21]
    with pytest.raises(ValueError,match='固件'):compile_profile(p,Capabilities(dac_bits=bits))
    for invalid in [replace(p,scan_step=1),replace(p,scan_points=0),replace(p,scan_points=65536),replace(p,scan_start=0,scan_step=-1),replace(p,scan_step=.5)]:
        with pytest.raises(ValueError):compile_profile(invalid,caps)
    compile_profile(replace(p,scan_points=65535,scan_step=0),caps)


@pytest.mark.parametrize('bits',[10,12,14])
@pytest.mark.parametrize('loop',[False,True])
def test_demo_first_point_signed_step_hold_loop_rearm_and_other_dacs(bits,loop):
    sim=ParallelSimulator(4,2,bits);dev=Device(sim);p=plan(bits,channel=1,trigger_input=1,trigger_edge='falling',scan_end='loop' if loop else 'hold')
    dev.upload(p);assert dev.status(1)['code']==p.initial
    dev.channel_command(ARM,1)
    for wrong in [(0,'falling'),(1,'rising')]:sim.external_trigger(*wrong)
    assert dev.status(1)['scan_position']==0
    for i in range(8):
        sim.external_trigger(1,'falling');s=dev.status(1)
        index=i%4 if loop else min(i,3)
        assert s['code']==p.scan_start+index*p.scan_step and s['scan_position']==index+1
        assert s['state']==(1 if loop or i<3 else 3)
        assert [dev.status(ch)['code'] for ch in (0,2,3)]==[p.initial]*3
    dev.channel_command(ABORT,1);assert dev.status(1)['code']==p.safe
    sim.external_trigger(1,'falling');assert dev.status(1)['code']==p.safe
    dev.channel_command(ARM,1);sim.external_trigger(1,'falling');assert dev.status(1)['scan_position']==1
    dev.channel_command(DISARM,1);assert dev.status(1)['code']==p.initial


def test_shared_input_software_step_and_atomic_invalid_upload():
    sim=ParallelSimulator();dev=Device(sim)
    for ch in range(4):dev.upload(plan(channel=ch,trigger_input=ch//2,scan_start=100+ch,scan_step=10))
    dev.group_command(ARM,[0,1,2,3]);sim.external_trigger(0,'rising')
    assert [dev.status(ch)['code'] for ch in range(4)]==[100,101,512,512]
    dev.group_command(ABORT,[0,1,2,3]);p=plan(trigger='software');dev.upload(p);dev.channel_command(ARM,0)
    dev.channel_command(TRIGGER,0);assert dev.status(0)['code']==p.scan_start
    dev.channel_command(ABORT,0);old=dev.status(0)
    bad=plan(scan_start=1023,scan_step=1)
    with pytest.raises(RuntimeError):dev.command(BEGIN_SCAN,packet(bad))
    assert dev.status(0)['config_id']==old['config_id'] and dev.status(0)['code']==old['code']


def run_rtl(tmp_path,tb):
    if not shutil.which('iverilog'):pytest.skip('iverilog required')
    (tmp_path/'tb.v').write_text(tb,encoding='ascii')
    sources=[str(p.resolve()) for p in Path('rtl/common').glob('*.v')]
    r=subprocess.run(['iverilog','-g2012','-s','tb_scan','-o',str(tmp_path/'sim'),*sources,str(tmp_path/'tb.v')],capture_output=True,text=True)
    assert r.returncode==0,r.stderr
    r=subprocess.run(['vvp',str(tmp_path/'sim')],cwd=tmp_path,capture_output=True,text=True,timeout=30)
    assert r.returncode==0,r.stdout+r.stderr


@pytest.mark.parametrize('bits',[10,12,14])
def test_rtl_uart_parallel_scan_validation_cycle_and_mode_switch(tmp_path,bits):
    mid=1<<(bits-1);limit=(1<<bits)-1;body=[]
    def cmd(op,data=b'',err=0):body.append(f"send(8'h{op:02x},192'h{int.from_bytes(data,'little'):048x},{err});")
    def expect(ch,code):body.append(f'if(codes[{ch*bits}+:{bits}]!={code})$fatal(1,"channel {ch} expected {code}");')
    cmd(STATUS)
    for ch in range(4):
        p=plan(bits,channel=ch,trigger_input=ch//2,scan_start=limit-ch*10)
        cmd(SELECT_CHANNEL,bytes([ch]));cmd(BEGIN_SCAN,packet(p));cmd(COMMIT)
    cmd(GROUP_COMMAND,bytes([ARM,15]));body.append('pulse(1,0);')
    expect(0,limit);expect(1,limit-10);expect(2,mid);expect(3,mid)
    body.append('pulse(1,0);');expect(0,limit-7);expect(1,limit-17)
    cmd(SELECT_CHANNEL,b'\0');cmd(STATUS);body.append('if(reply[207:192]!=2 || !reply[235] || reply[63:56]!=1)$fatal(1,"scan status");')
    cmd(GROUP_COMMAND,bytes([ABORT,15]));expect(0,mid+1)
    # Malformed endpoint, reserved flags and wrong channel must leave committed output untouched.
    for p in [plan(bits,scan_step=1),plan(bits,scan_start=0,scan_step=-7),plan(bits,scan_points=0),plan(bits,channel=1)]:cmd(BEGIN_SCAN,packet(p),3)
    cmd(BEGIN_SCAN,packet(plan(bits))[:-1]+bytes([8]),3);expect(0,mid+1)
    # Software-driven one-point loop accepts each command once, including duplicate request retries.
    p=plan(bits,scan_start=12,scan_step=-2,scan_points=1,trigger='software',scan_end='loop')
    cmd(BEGIN_SCAN,packet(p));cmd(COMMIT);cmd(ARM);cmd(TRIGGER);expect(0,12)
    body.append('seq=seq-1;');cmd(TRIGGER);cmd(STATUS);body.append('if(reply[103:88]!=1 || reply[207:192]!=1)$fatal(1,"duplicate advanced scan");')
    cmd(TRIGGER);cmd(STATUS);body.append('if(reply[103:88]!=2 || reply[63:56]!=1)$fatal(1,"loop");')
    cmd(ABORT)
    # A RAM-backed constant can still be committed after a scan.
    cmd(0x30,struct.pack('<HHHHIBBBBB',mid,mid,1,1,321,0,0,0,0,1));cmd(0x31,struct.pack('<HHI',0,mid,8));cmd(COMMIT);cmd(ARM);cmd(TRIGGER)
    body.append('repeat(20)tick;');expect(0,mid);cmd(STATUS);body.append('if(reply[235])$fatal(1,"scan mode retained after waveform");')
    tb=f'''`timescale 1ns/1ps
module tb_scan;
reg clk=0,rst=1;always #2.5 clk=~clk;
reg [255:0] req=0;reg valid=0;wire [239:0] reply;wire rv;
reg [1:0] rise=0,fall=0;wire [{bits*4-1}:0] codes;integer seq=0;
parallel_controller #(.DAC_BITS({bits})) dut(clk,rst,req,valid,reply,rv,1'b1,rise,fall,codes);
task tick;begin @(posedge clk);#0.1;end endtask
task pulse;input [1:0] r,f;begin @(negedge clk);rise=r;fall=f;tick;@(negedge clk);rise=0;fall=0;tick;end endtask
task send;input [7:0] op;input [191:0] payload;input [7:0] err;begin
@(negedge clk);seq=seq+1;req={{16'b0,payload,seq[15:0],op,8'd1,16'h5aa5}};valid=1;tick;@(negedge clk);valid=0;wait(rv);#0.1;
if(reply[55:48]!=err)$fatal(1,"op %h got error %d expected %d seq %d",op,reply[55:48],err,seq);tick;
end endtask
initial begin repeat(5)tick;rst=0;tick;{''.join(body)} $display("PASS parallel scan protocol");$finish;end
initial begin #100000;$fatal(1,"timeout");end
endmodule'''
    run_rtl(tmp_path,tb)


@pytest.mark.parametrize('bits',[10,12,14])
def test_rtl_boot_and_each_adjacent_trigger(tmp_path,bits):
    from host.boot import generate_boot
    from host.board import Board
    p=plan(bits,scan_start=2,scan_step=3,scan_points=4,scan_end='hold')
    folder=tmp_path/'boot';generate_boot(Board(dac_count=4,dac_bits=bits,boot_profiles={'0':p.to_dict()}),folder)
    wrapper=(folder/'ramp_boot_top.v').read_text();configs=wrapper.split(".BOOT_CONFIGS(2048'h")[1].split(')')[0]
    shutil.copy2(folder/'boot_dac0.mem',tmp_path/'boot_dac0.mem')
    mid=p.initial
    tb=f'''`timescale 1ns/1ps
module tb_scan;
reg clk=0,rst=1;always #2.5 clk=~clk;reg [1:0] rise=0;wire [{bits-1}:0] code;wire [5:0] monitor;wire [239:0] reply;wire rv;integer i;
ramp_controller #(.DAC_COUNT(1),.DAC_BITS({bits}),.RUN_ADDR_BITS(11),.BOOT_CONFIG(256'h{configs[-64:]}),.BOOT_MEMORY_FILE("boot_dac0.mem")) dut(clk,rst,256'b0,1'b0,reply,rv,1'b1,rise,2'b0,code,monitor);
task tick;begin @(posedge clk);#0.1;end endtask
initial begin repeat(5)tick;rst=0;repeat(3)tick;if(code!={mid} || !monitor[2])$fatal(1,"boot not armed");
@(negedge clk);rise=1;
for(i=0;i<4;i=i+1)begin tick;if(code!=2+i*3)$fatal(1,"adjacent trigger %d code %d",i,code);end
repeat(3)tick;if(code!=11 || monitor[1:0]!=3)$fatal(1,"hold final point");
@(negedge clk);rise=0;rst=1;repeat(3)tick;rst=0;repeat(3)tick;
if(dut.active_valid)$fatal(1,"boot incorrectly resurrected after clock reset");
$display("PASS boot scan and consecutive core events");$finish;end
initial begin #10000;$fatal(1,"timeout");end
endmodule'''
    run_rtl(tmp_path,tb)


def test_scan_ui_saved_snapshot_draft_and_demo(tmp_path,monkeypatch):
    from PySide6.QtWidgets import QApplication,QMessageBox
    from host.app import Studio
    from host.storage import Library
    from tests.test_gui import until
    app=QApplication.instance() or QApplication([]);w=Studio(Library(tmp_path));w.show()
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.Discard)
    try:
        w.board_page.dac_count.setValue(4);s=w.scan_page;s.load(plan(),False);w.designer_tabs.setCurrentIndex(2)
        assert w.mode.count()==2 and s.save_button.isEnabled();assert s.save()
        p=copy.deepcopy(s.profile);w.open_dac(2);cp=w.channel_page;cp.plan.setCurrentIndex(cp.plan.findData(p.id))
        for i,check in enumerate(w.board_page.flash_page.boot_checks):check.setChecked(i==2)
        w.board_page.flash_page.capture_boot()
        assert w.board_page.flash_page.boot_profiles['2']['mode']=='scan'
        w.connect_button.click();until(app,lambda:w.connected and len(w.channel_statuses)==4)
        cp.buttons['prepare'].click();until(app,lambda:not w.busy and w.channel_statuses[2]['state']==1)
        cp.buttons['external'].click();until(app,lambda:not w.busy and w.channel_statuses[2]['scan_position']==1)
        assert w.channel_statuses[2]['code']==p.scan_start and cp.plot.curves[0][0][3]=='scan'
        cp.edit_plan();assert w.designer_tabs.currentIndex()==2
        s.step.setValue(-10);assert s.save();cp.refresh()
        assert cp.plot.profile.scan_step==-7 and cp.profile().scan_step==-10
        s.step.setValue(100);assert not s.valid and not s.save_button.isEnabled()
        cp.buttons['abort'].click();until(app,lambda:not w.busy)
        w.send('disconnect',None);until(app,lambda:not w.connected and not w.busy)
    finally:w.close();app.processEvents()
    w=Studio(Library(tmp_path))
    try:assert w.scan_page.step.value()==100 and not w.scan_page.valid
    finally:w.close();app.processEvents()


def test_scan_physical_level_is_one_event_and_counters_saturate(tmp_path):
    tb='''`timescale 1ns/1ps
module tb_scan;
reg clk=0,rst=1,arm=0,abort_run=0,level=0,raw_mode=0,raw=0;always #2.5 clk=~clk;
wire pulse,fall;wire [1:0] state;wire ready;wire [9:0] code;wire [15:0] position,loops;wire [31:0] ignored;
trigger_sync sync(clk,rst,level,pulse,fall);
scan_engine engine(clk,rst,arm,1'b0,abort_run,1'b0,raw_mode ? raw:pulse,1'b1,1'b1,10'd512,10'd511,10'd100,16'sd0,16'd1,1'b1,state,ready,code,position,loops,ignored);
task tick;begin @(posedge clk);#0.1;end endtask
initial begin repeat(4)tick;rst=0;repeat(4)tick;@(negedge clk);arm=1;tick;@(negedge clk);arm=0;level=1;
repeat(20)tick;if(loops!=1 || position!=1 || code!=100)$fatal(1,"high level counted more than once");
@(negedge clk);level=0;repeat(5)tick;@(negedge clk);level=1;repeat(5)tick;if(loops!=2)$fatal(1,"second physical event lost");
@(negedge clk);raw_mode=1;raw=1;repeat(65536)tick;
if(loops!=65535 || position!=1 || code!=100 || state!=1)$fatal(1,"loop counter overflow");
@(negedge clk);abort_run=1;tick;if(code!=511 || state!=0)$fatal(1,"abort priority");
@(negedge clk);abort_run=0;repeat(3)tick;if(code!=511)$fatal(1,"event moved aborted output");
$display("PASS physical trigger and saturated loops");$finish;end
initial begin #500000;$fatal(1,"timeout");end
endmodule'''
    run_rtl(tmp_path,tb)
