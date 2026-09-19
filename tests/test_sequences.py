import copy,json,os,struct,subprocess,shutil
from pathlib import Path
from dataclasses import replace
import pytest
from host.model import Profile,Capabilities,compile_profile,fingerprint
from host.functions import compile_samples
from host.sequences import compile_sequence,pack_step
from host.protocol import Device,ARM,ABORT,DISARM,BEGIN_SEQUENCE,WRITE_STEP,COMMIT
from host.parallel_simulator import ParallelSimulator


def example(bits=10,events=False,shared=False):
    mid=1<<(bits-1)
    w=Profile(mode='ramp',name='脉冲',dac_bits=bits,initial=mid,safe=mid,amplitude=mid-4,rise_ms=.000015,hold_ms=.00001,fall_ms=.000015)
    steps=[dict(waveform=w.to_dict(),kind='edge',start_ms=0,delay_ms=0,input=0,edge='rising',repeat=2),
           dict(waveform=replace(w,amplitude=mid-8).to_dict(),kind='gate' if events else 'edge',start_ms=.0001,delay_ms=0,input=0 if shared else 1,edge='rising',repeat=1),
           dict(waveform=w.to_dict(),kind='edge',start_ms=.0002,delay_ms=0,input=0,edge='falling',repeat=1)]
    return Profile(mode='sequence',name='测试序列',dac_bits=bits,initial=mid,safe=mid+2,sequence_mode='events' if events else 'timeline',sequence_steps=steps)


@pytest.mark.parametrize('bits',[10,12,14])
def test_timeline_samples_gaps_and_snapshot(bits):
    p=example(bits);c=compile_sequence(p);actual=[v for v,n in c.runs for _ in range(n)]
    expected=[p.initial]*48
    for s in p.sequence_steps:
        w=Profile.from_dict(s['waveform']);d=compile_samples(w);wave=[v for v,n in d.runs for _ in range(n)]*s['repeat'];start=round(s['start_ms']*200000);expected[start:start+len(wave)]=wave
    assert actual==expected
    assert c.total==48
    saved=Profile.from_dict(json.loads(json.dumps(p.to_dict())))
    assert fingerprint(saved)==fingerprint(p)
    p.sequence_steps[1]['start_ms']=.00001
    with pytest.raises(ValueError,match='重叠'):compile_sequence(p)


def test_long_waits_split_counts_and_same_wave_pool():
    p=example();p.sequence_steps[1]['start_ms']=30000;p.sequence_steps[2]['start_ms']=60000
    c=compile_sequence(p);assert all(0<n<=0xffffffff for _,n in c.runs);assert c.total==12_000_000_008
    q=example(events=True);c=compile_sequence(q)
    assert c.steps[0][4]==c.steps[2][4]
    with pytest.raises(ValueError,match='新版'):compile_profile(q,Capabilities())
    q.sequence_steps[1]['input']=2
    with pytest.raises(ValueError,match='输入'):compile_profile(q,Capabilities(trigger_count=2,sequence_steps=64))


@pytest.mark.parametrize('shared',[False,True])
def test_demo_event_gate_routing_and_abort(monkeypatch,shared):
    now=[1.];monkeypatch.setattr('host.simulator.time.monotonic',lambda:now[0])
    sim=ParallelSimulator();dev=Device(sim);p=example(events=True,shared=shared);dev.upload(p);dev.channel_command(ARM,0)
    now[0]+=.001
    sim.external_trigger(1,'rising');assert dev.status(0)['state']==1
    sim.external_trigger(0,'rising');now[0]+=.001;s=dev.status(0);assert s['sequence_step']==1
    now[0]+=.001;source=0 if shared else 1;sim.external_trigger(1-source,'rising');assert dev.status(0)['state']==1
    sim.external_trigger(source,'rising');now[0]+=.001;s=dev.status(0);assert s['state']==2 and s['code']==p.initial-8
    sim.external_trigger(1-source,'falling');assert dev.status(0)['state']==2
    sim.external_trigger(source,'falling');now[0]+=.001;s=dev.status(0);assert s['sequence_step']==2
    now[0]+=.001;sim.external_trigger(0,'rising');assert dev.status(0)['state']==1
    sim.external_trigger(0,'falling');now[0]+=.001;s=dev.status(0);assert s['sequence_step']==0 and s['loops']==1
    dev.channel_command(ABORT,0);now[0]+=1;sim.external_trigger(0,'rising');assert dev.status(0)['code']==p.safe
    assert [s['code'] for s in dev.status_all()[1:]]==[512]*3


def test_partial_program_transaction_keeps_old_active():
    dev=Device(ParallelSimulator());dev.set_static(0,513,512)
    dev.command(BEGIN_SEQUENCE,struct.pack('<HHHHIB',512,512,2,2,123,0))
    with pytest.raises(RuntimeError):dev.command(COMMIT)
    assert dev.status(0)['code']==513
    step=list(compile_sequence(example(events=True)).steps[0]);step[4]=0xffff
    with pytest.raises(RuntimeError):dev.command(WRITE_STEP,pack_step(step))


def test_sequence_editor_saved_snapshot_and_channel_demo(tmp_path,monkeypatch):
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
        p=example(events=True);p.name='多输入 · Ramp → 门控 → Ramp'
        w.sequence_page.load(p,saved=False);w.designer_tabs.setCurrentIndex(1)
        page=w.sequence_page;page.save();assert any(x.id==p.id for x in lib.list())
        assert not any(w.profile_list.item(i).data(256).mode=='sequence' for i in range(w.profile_list.count()))
        page.table.selectRow(1);page.step_source.setCurrentIndex(page.step_source.findData(0));assert page.collect().sequence_steps[1]['input']==0
        page.step_source.setCurrentIndex(page.step_source.findData(1));page.save()
        before=fingerprint(page.collect());page.time.setUnit('ns');assert fingerprint(page.collect())==before
        preview=Path('build/sequence_preview');preview.mkdir(exist_ok=True);QTest.qWait(200);w.grab().save(str(preview/'events.png'))
        w.connect_button.click();until(app,lambda:w.connected and len(w.channel_statuses)==4)
        w.open_dac(0);cp=w.channel_page;cp.plan.setCurrentIndex(cp.plan.findData(p.id));cp.buttons['upload'].click()
        until(app,lambda:not w.busy and cp.buttons['arm'].isEnabled());cp.buttons['arm'].click();until(app,lambda:not w.busy and w.channel_statuses[0]['prepared'])
        cp.buttons['external'].click();until(app,lambda:not w.busy and w.channel_statuses[0]['sequence_step']==1)
        assert w.channel_statuses[0]['trigger_input']==1
        cp.buttons['external'].click();until(app,lambda:not w.busy and w.channel_statuses[0]['state']==2)
        assert cp.buttons['falling'].isEnabled();cp.buttons['falling'].click();until(app,lambda:not w.busy and w.channel_statuses[0]['sequence_step']==2)
        cp.buttons['external'].click();until(app,lambda:not w.busy and w.channel_statuses[0]['loops']==1)
        cp.buttons['abort'].click();until(app,lambda:not w.busy and w.channel_statuses[0]['state']==0)
        QTest.qWait(200);w.grab().save(str(preview/'channel.png'))
        timeline=example();timeline.name='时间表 · 三波形';w.sequence_page.load(timeline,False);w.sequence_page.save();w.navigate(0);w.designer_tabs.setCurrentIndex(1)
        QTest.qWait(200);w.grab().save(str(preview/'timeline.png'))
        page.name.setText('未保存的序列草稿')
        assert not errors,errors
    finally:
        if w.connected:
            w.send("disconnect",None);until(app,lambda:not w.connected and not w.busy)
        w.close();app.processEvents()
    restored=Studio(Library(tmp_path))
    try:assert restored.sequence_page.name.text()=='未保存的序列草稿'
    finally:restored.close();app.processEvents()


@pytest.mark.parametrize('bits',[10,12,14])
@pytest.mark.parametrize('shared',[False,True])
def test_program_rtl_events_and_boot(tmp_path,bits,shared):
    from host.board import Board
    from host.boot import generate_boot
    if not shutil.which('iverilog'):pytest.skip('iverilog required')
    p=example(bits,True,shared);c=compile_sequence(p);mid=p.initial
    out=tmp_path/'boot';manifest=generate_boot(Board(dac_count=4,dac_bits=bits,boot_profiles={'0':p.to_dict()}),out)
    cfg=1|4|16|(p.initial<<16)|(p.safe<<32)|(1<<48)|(123<<64)|(len(c.runs)<<96)|(len(c.steps)<<184)
    # One boot-configured channel exercises the same controller/engines and memory path.
    tb=f'''`timescale 1ns/1ps
module tb_program;
reg clk=0,rst=1;always #2.5 clk=~clk;
reg [255:0] request=0;reg valid=0;wire [239:0] response;wire response_valid;
reg [1:0] rise=0,fall=0;wire [{bits-1}:0] code;wire [5:0] monitor;
ramp_controller #(.DAC_COUNT(1),.TRIGGER_COUNT(2),.DAC_BITS({bits}),.RUN_ADDR_BITS(11),.BOOT_CONFIG(256'h{cfg:064x}),.BOOT_MEMORY_FILE("boot_dac0.mem")) dut(clk,rst,request,valid,response,response_valid,1'b1,rise,fall,code,monitor);
task tick;begin @(posedge clk);#0.1;end endtask
task edge_event;input integer source;input integer falling;begin @(negedge clk);if(falling)fall=1<<source;else rise=1<<source;tick;@(negedge clk);fall=0;rise=0;end endtask
task ready;input integer step;begin wait(dut.program_step==step && dut.program_prepared);tick;end endtask
task command;input [7:0] cmd;begin @(negedge clk);request={{16'b0,192'b0,16'd1,cmd,8'd1,16'h5aa5}};valid=1;tick;@(negedge clk);valid=0;wait(response_valid);tick;end endtask
initial begin
repeat(5)tick;rst=0;ready(0);
if(code!={mid})$fatal(1,"initial");
edge_event(1,0);repeat(8)tick;if(dut.program_state!=1)$fatal(1,"wrong input starts");
edge_event(0,0);ready(1);edge_event({1 if shared else 0},0);repeat(8)tick;if(dut.program_state!=1)$fatal(1,"second wrong route");
edge_event({0 if shared else 1},0);repeat(18)tick;if(code!={mid-8} || dut.program_state!=2)$fatal(1,"gate hold %d",code);
edge_event({1 if shared else 0},1);repeat(8)tick;if(dut.program_state!=2)$fatal(1,"wrong fall");
edge_event({0 if shared else 1},1);ready(2);
edge_event(0,0);repeat(8)tick;if(dut.program_state!=1)$fatal(1,"wrong edge");
edge_event(0,1);ready(0);if(dut.program_loops!=1)$fatal(1,"whole loops");
command(8'h22);repeat(5)tick;if(code!={mid+2} || monitor[1:0]!=0)$fatal(1,"abort %d",code);
edge_event(0,0);repeat(10)tick;if(code!={mid+2})$fatal(1,"abort rearm");
rst=1;repeat(5)tick;rst=0;repeat(20)tick;if(monitor[3] || code!={mid})$fatal(1,"reset boot protection");
$display("PASS program bits={bits} shared={shared}");$finish;
end
initial begin #50000;$fatal(1,"timeout phase=%d step=%d engine=%d",dut.program_phase,dut.program_step,dut.fixed_state);end
endmodule
'''
    (out/'tb.v').write_text(tb,encoding='utf-8')
    sources=[str(p.resolve()) for p in Path('rtl/common').glob('*.v')]
    r=subprocess.run(['iverilog','-g2012','-s','tb_program','-o',str(out/'sim'),*sources,str(out/'tb.v')],capture_output=True,text=True)
    assert r.returncode==0,r.stderr
    r=subprocess.run(['vvp',str(out/'sim')],cwd=out,capture_output=True,text=True,timeout=20)
    assert r.returncode==0,r.stdout+r.stderr
    assert 'PASS program' in r.stdout


def test_uart_program_commit_validation_and_sample_boundaries(tmp_path):
    """Drive real command decoder, check failed commits and every timeline output clock."""
    if not shutil.which('iverilog'):pytest.skip('iverilog required')
    p=example(events=True);c=compile_sequence(p)
    requests=[]
    def cmd(op,payload=b'',error=0):
        from host.protocol import encode
        requests.append((encode(op,len(requests)+1,payload),error))
    cmd(BEGIN_SEQUENCE,struct.pack('<HHHHIB',512,514,len(c.runs),len(c.steps),99,0))
    cmd(COMMIT,error=4)
    invalid=list(c.steps[0]);invalid[4]=65535;cmd(WRITE_STEP,pack_step(invalid),3)
    for step in c.steps:cmd(WRITE_STEP,pack_step(step))
    for i,(v,n) in enumerate(c.runs):cmd(0x31,struct.pack('<HHI',i,v,n))
    cmd(COMMIT);cmd(ARM)
    vectors='\n'.join(f'{int.from_bytes(f,"little"):064x}' for f,_ in requests)
    (tmp_path/'vectors.mem').write_text(vectors)
    (tmp_path/'errors.mem').write_text('\n'.join(f'{e:02x}' for _,e in requests))
    # Replay a fixed timeline via the same hardware upload path after event abort.
    timeline=example();d=compile_sequence(timeline);gold=[v for v,n in d.runs for _ in range(n)]
    (tmp_path/'expected.mem').write_text('\n'.join(f'{v:04x}' for v in gold))
    setup=[]
    def statement(op,payload=b''):
        setup.append(f"send(8'h{op:02x},192'h{int.from_bytes(payload,'little'):048x},0);")
    statement(ABORT);statement(0x30,struct.pack('<HHHHIBBBBB',512,514,len(d.runs),1,100,0,0,0,0,1))
    for i,(v,n) in enumerate(d.runs):statement(0x31,struct.pack('<HHI',i,v,n))
    statement(COMMIT);statement(ARM)
    root=Path.cwd();tb=f'''`timescale 1ns/1ps
module tb_program_upload;
reg clk=0,rst=1;always #2.5 clk=~clk;reg [255:0] req=0;reg valid=0;wire [239:0] reply;wire rv;
reg [1:0] rise=0,fall=0;wire [9:0] code;wire [5:0] monitor;
reg [255:0] vectors[0:{len(requests)-1}];reg [7:0] errors[0:{len(requests)-1}];reg [15:0] expected[0:{len(gold)-1}];
integer i,seq=100;
ramp_controller #(.DAC_COUNT(1),.RUN_ADDR_BITS(11)) dut(clk,rst,req,valid,reply,rv,1'b1,rise,fall,code,monitor);
task tick;begin @(posedge clk);#0.1;end endtask
task send;input [7:0] op;input [191:0] p;input [7:0] err;begin
@(negedge clk);seq=seq+1;req={{16'b0,p,seq[15:0],op,8'd1,16'h5aa5}};valid=1;tick;@(negedge clk);valid=0;wait(rv);#0.1;
if(reply[55:48]!=err)$fatal(1,"command %h error %d",op,reply[55:48]);tick;end endtask
initial begin
$readmemh("vectors.mem",vectors);$readmemh("errors.mem",errors);$readmemh("expected.mem",expected);
repeat(5)tick;rst=0;
for(i=0;i<{len(requests)};i=i+1)send(vectors[i][31:24],vectors[i][239:48],errors[i]);
if(!dut.active_program || !dut.program_prepared)$fatal(1,"program commit");
@(negedge clk);rise=1;tick;@(negedge clk);rise=0;
wait(dut.program_step==1 && dut.program_prepared);if(dut.program_source!=1)$fatal(1,"uploaded route");
{''.join(setup)}
// Inject the internal software pulse only here to align the observer to its exact acceptance clock.
@(negedge clk);force dut.soft_trigger=1;tick;
if(code!==expected[0])$fatal(1,"first sample");
@(negedge clk);release dut.soft_trigger;
for(i=1;i<{len(gold)};i=i+1)begin tick;if(code!==expected[i])$fatal(1,"sample %d got %d expected %d",i,code,expected[i]);end
tick;if(code!=512 || monitor[1:0]!=3)$fatal(1,"end");
$display("PASS upload and timeline clocks");$finish;
end
initial begin #100000;$fatal(1,"timeout");end
endmodule
'''
    (tmp_path/'tb.v').write_text(tb)
    sources=[str(f.resolve()) for f in Path('rtl/common').glob('*.v')]
    r=subprocess.run(['iverilog','-g2012','-s','tb_program_upload','-o',str(tmp_path/'sim'),*sources,str(tmp_path/'tb.v')],capture_output=True,text=True)
    assert r.returncode==0,r.stderr
    r=subprocess.run(['vvp',str(tmp_path/'sim')],cwd=tmp_path,capture_output=True,text=True,timeout=20)
    assert r.returncode==0,r.stdout+r.stderr


@pytest.mark.parametrize('bits',[10,12,14])
def test_program_delay_samples_and_adjacent_gate_edges(tmp_path,bits):
    """Check the timed step's entire output and a gate falling on the next clock."""
    from host.board import Board
    from host.boot import generate_boot
    if not shutil.which('iverilog'):pytest.skip('iverilog required')
    p=example(bits,True);wave=Profile.from_dict(p.sequence_steps[0]['waveform'])
    delayed={**p.sequence_steps[0],'kind':'delay','delay_ms':.0001,'repeat':3}
    gate=copy.deepcopy(p.sequence_steps[1]);gate['waveform']=replace(wave,amplitude=p.initial-80,rise_ms=.0002,fall_ms=.00015).to_dict()
    p.sequence_steps=copy.deepcopy([p.sequence_steps[0],delayed,gate,p.sequence_steps[2]]*16)
    c=compile_sequence(p);d=compile_samples(wave);gold=[v for v,n in d.runs for _ in range(n)]*3
    out=tmp_path/'boot';generate_boot(Board(dac_count=4,dac_bits=bits,boot_profiles={'0':p.to_dict()}),out)
    cfg=1|4|16|(p.initial<<16)|(p.safe<<32)|(1<<48)|(len(c.runs)<<96)|(64<<184)
    (out/'expected.mem').write_text('\n'.join(f'{v:04x}' for v in gold))
    tb=f'''`timescale 1ns/1ps
module tb_delay;
reg clk=0,rst=1;always #2.5 clk=~clk;
reg [1:0] rise=0,fall=0;wire [{bits-1}:0] code;wire [239:0] reply;wire rv;wire [5:0] monitor;
reg [15:0] expected[0:{len(gold)-1}];integer i,block;
ramp_controller #(.DAC_COUNT(1),.TRIGGER_COUNT(2),.DAC_BITS({bits}),.RUN_ADDR_BITS(11),.LEGACY_ENABLED(0),
 .BOOT_CONFIG(256'h{cfg:064x}),.BOOT_MEMORY_FILE("boot_dac0.mem")) dut(clk,rst,256'b0,1'b0,reply,rv,1'b1,rise,fall,code,monitor);
task tick;begin @(posedge clk);#0.1;end endtask
task ready;input integer step;begin wait(dut.program_step==step && dut.program_prepared);tick;end endtask
initial begin
$readmemh("expected.mem",expected);repeat(5)tick;rst=0;ready(0);
@(negedge clk);rise=1;tick;@(negedge clk);rise=0;
wait(dut.program_step==1 && dut.program_phase==2);#0.1;
for(i=0;i<20;i=i+1)begin tick;if(code!={p.initial} || dut.fixed_state==2)$fatal(1,"delay shortened");end
wait(dut.fixed_state==2);#0.1;
for(i=0;i<{len(gold)};i=i+1)begin
 if(code!==expected[i])$fatal(1,"delayed step sample %d got %d expected %d",i,code,expected[i]);tick;
end
ready(2);
// One-cycle high at the synchronizer output. Its falling edge must not be lost.
@(negedge clk);rise=2;tick;@(negedge clk);rise=0;fall=2;tick;@(negedge clk);fall=0;
ready(3);if(code!={p.initial})$fatal(1,"early return");
@(negedge clk);fall=1;tick;@(negedge clk);fall=0;
for(block=4;block<64;block=block+4)begin
 ready(block);@(negedge clk);rise=1;tick;@(negedge clk);rise=0;
 ready(block+2);@(negedge clk);rise=2;tick;@(negedge clk);rise=0;fall=2;tick;@(negedge clk);fall=0;
 ready(block+3);@(negedge clk);fall=1;tick;@(negedge clk);fall=0;
end
ready(0);if(dut.program_loops!=1)$fatal(1,"loop");
$display("PASS delay and adjacent gate edges bits={bits}");$finish;
end
initial begin #100000;$fatal(1,"timeout step=%d phase=%d",dut.program_step,dut.program_phase);end
endmodule
'''
    (out/'tb.v').write_text(tb)
    sources=[str(f.resolve()) for f in Path('rtl/common').glob('*.v')]
    r=subprocess.run(['iverilog','-g2012','-s','tb_delay','-o',str(out/'sim'),*sources,str(out/'tb.v')],capture_output=True,text=True)
    assert r.returncode==0,r.stderr
    r=subprocess.run(['vvp',str(out/'sim')],cwd=out,capture_output=True,text=True,timeout=20)
    assert r.returncode==0,r.stdout+r.stderr
