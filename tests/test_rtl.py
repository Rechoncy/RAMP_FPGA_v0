import shutil
import subprocess
import struct
import random
from pathlib import Path
import pytest
from host.protocol import encode,FrameParser,INFO,STATUS,BEGIN,SEGMENT,COMMIT,ARM,TRIGGER,ABORT,DISARM

ROOT=Path(__file__).resolve().parents[1]


def run_tb(name,sources):
    if "rtl/common/packet_link.v" in sources and "rtl/common/uart_autobaud.v" not in sources:sources=[*sources,"rtl/common/uart_autobaud.v"]
    if "rtl/common/controller.v" in sources and "rtl/common/linear_engine.v" not in sources:sources=[*sources,"rtl/common/linear_engine.v"]
    if "rtl/common/controller.v" in sources and "rtl/common/scan_engine.v" not in sources:sources=[*sources,"rtl/common/scan_engine.v"]
    if "rtl/common/controller.v" in sources and "rtl/common/program_sequencer.v" not in sources:sources=[*sources,"rtl/common/program_sequencer.v"]
    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("Icarus Verilog is not installed")
    output=ROOT/"build"/(name+".vvp"); output.parent.mkdir(exist_ok=True)
    subprocess.run(["iverilog","-g2012","-s",name,"-o",str(output),*sources,f"tests/rtl/{name}.v"],cwd=ROOT,check=True,capture_output=True,text=True)
    result=subprocess.run(["vvp",str(output)],cwd=ROOT,check=True,capture_output=True,text=True,timeout=40)
    assert "PASS" in result.stdout


def test_sequence_timing_and_boundaries():
    run_tb("tb_sequence",["rtl/common/sequence_engine.v"])


def test_random_every_sample_and_64_segment_capacity():
    rng=random.Random(20260910); descriptors=[]; parameters=[]
    for case in range(12):
        count=[1,2,3,64][case%4]; start=rng.randrange(1024); previous=start; repeats=1+case%3
        parameters.extend([start,count,repeats,16+(case%3)])
        for i in range(count):
            target=start if i==count-1 and repeats>1 else rng.randrange(1024)
            n=1 if case%3==0 else rng.randrange(1,25)
            q,r=divmod(abs(target-previous),n)
            descriptors.append((target<<74)|(q<<64)|(r<<32)|n)
            previous=target
        descriptors.extend([0]*(64-count))
    (ROOT/"build").mkdir(exist_ok=True)
    (ROOT/"build/random_descriptors.hex").write_text("\n".join(f"{d:021x}" for d in descriptors))
    (ROOT/"build/random_parameters.hex").write_text("\n".join(f"{p:08x}" for p in parameters))
    run_tb("tb_random",["rtl/common/sequence_engine.v"])


def test_serial_wire_protocol_controller_and_responses():
    requests=[]
    def add(command,payload=b""):
        requests.append(encode(command,len(requests)+1,payload))
    config=struct.pack("<HHHHIBxHHHH",100,17,2,1,0xABCDEF12,0,20,0,1,1024)
    add(INFO); add(ARM) # no active config -> incomplete
    add(BEGIN,config); add(SEGMENT,struct.pack("<HHI",0,113,7)); add(COMMIT) # incomplete
    add(SEGMENT,struct.pack("<HHI",1,100,5)); add(COMMIT); add(STATUS)
    add(ARM); add(BEGIN,config) # cannot replace while armed
    add(TRIGGER); requests.append(requests[-1]) # exact duplicate must not retrigger
    add(STATUS); add(ABORT); add(STATUS)
    add(BEGIN,config); add(SEGMENT,struct.pack("<HHI",0,1024,5)); add(DISARM)
    assert len(requests)==18
    (ROOT/"build").mkdir(exist_ok=True)
    (ROOT/"build/protocol_vectors.hex").write_text("\n".join(f"{int.from_bytes(f,'little'):064x}" for f in requests),encoding="ascii")
    run_tb("tb_protocol",["rtl/common/uart.v","rtl/common/packet_link.v","rtl/common/controller.v","rtl/common/gate_engine.v","rtl/common/sequence_engine.v","rtl/common/sample_engine.v"])
    responses=[int(line,16).to_bytes(32,"little") for line in (ROOT/"build/protocol_responses.hex").read_text().splitlines()]
    expected_errors=[0,4,0,0,4,0,0,0,0,2,0,0,0,0,0,0,3,0]
    assert responses[0][16]==1, "GET_INFO must advertise DAC clock divider 1"
    for request,response,error in zip(requests,responses,expected_errors):
        assert FrameParser().feed(response)==[response],"CRC mismatch in RTL response"
        assert response[3]==request[3]|128 and response[4:6]==request[4:6]
        assert response[6]==error
    assert responses[10]==responses[11],"Duplicate trigger must return cached response"
    status=responses[12][6:30]
    assert status[1]==3 and struct.unpack_from("<H",status,3)[0]==100
    assert struct.unpack_from("<H",status,5)[0]==1
    assert struct.unpack_from("<I",status,7)[0]==0xABCDEF12
    assert struct.unpack_from("<H",responses[14],9)[0]==20
