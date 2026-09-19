"""No USB access: transport framing, partial writes, retry identity and negotiation."""
import struct

import pytest

from host.model import Capabilities, Profile
from host.protocol import Device, INFO, BEGIN_RUNS, COMMIT, RUN, RUN_BATCH, encode
from host.parallel_simulator import ParallelSimulator
from host.usb_transport import USBTransport, D2XXError, enumerate_usb_devices


def test_ft232h_digilent_jtag_is_not_offered_or_opened_as_fifo():
    from host.usb_transport import D2XXBackend
    rows=[(8,b'DATA',b'USB <-> Serial Converter'),
          (8,b'PROGRAMMER',b'Digilent USB Device'),
          (8,b'JTAG',b'FT232H JTAG Adapter'),
          (6,b'2232',b'Dual RS232-HS')]
    backend=D2XXBackend.__new__(D2XXBackend)
    calls=[]
    def call(name,*args):
        calls.append(name)
        if name=='FT_CreateDeviceInfoList':args[0]._obj.value=len(rows)
        elif name=='FT_GetDeviceInfoDetail':
            kind,serial,description=rows[args[0]]
            args[2]._obj.value=kind;args[5].value=serial;args[6].value=description
        else:raise AssertionError(f'Unexpected device mutation: {name}')
    backend.call=call
    assert [d['serial'] for d in backend.enumerate_devices()]==['DATA']
    with pytest.raises(D2XXError,match='唯一'):
        backend.open_device('PROGRAMMER')
    assert 'FT_OpenEx' not in calls and 'FT_SetBitMode' not in calls


class Handle:
    def __init__(self):
        self.closed=False;self.out=bytearray();self.partial=bytearray();self.packets=[]
        self.sim=ParallelSimulator(4,2,10);self.drop=0;self.noise=True
    def configure_fifo(self, **kwargs):self.settings=kwargs
    def write(self,data):
        count=min(len(data),7);self.partial.extend(data[:count])
        while len(self.partial)>=32:
            frame=bytes(self.partial[:32]);del self.partial[:32];self.packets.append(frame)
            if self.drop:self.drop-=1
            else:
                reply=self.sim.exchange(frame)
                if self.noise:
                    corrupt=bytearray(reply);corrupt[-1]^=1
                    self.out.extend(b'noise'+corrupt+encode(0x81,frame[4]^1,b'stale'))
                self.out.extend(reply)
        return count
    def read(self,size):
        data=bytes(self.out[:min(size,11)]);del self.out[:len(data)];return data
    def close(self):self.closed=True


class Backend:
    def __init__(self):self.handle=Handle();self.serial=None
    def enumerate_devices(self):return [dict(port='usb:TEST',serial='TEST',description='FT232H',type=8)]
    def open_device(self,serial):self.serial=serial;return self.handle


def test_usb_partial_writes_fragmented_crc_noise_and_endpoint():
    backend=Backend();assert enumerate_usb_devices(backend)[0]['port']=='usb:TEST'
    transport=USBTransport('usb:TEST',backend=backend)
    device=Device(transport)
    assert device.caps.dac_count==4 and backend.serial=='TEST'
    assert len(backend.handle.packets)==1 and not backend.handle.partial
    assert backend.handle.settings==dict(read_timeout_ms=50,write_timeout_ms=700)
    transport.close();transport.close();assert backend.handle.closed
    with pytest.raises(D2XXError,match='关闭'):device.refresh_capabilities()


def test_usb_retry_keeps_exact_sequence_and_request():
    backend=Backend();backend.handle.drop=1
    transport=USBTransport('usb:TEST',backend=backend,timeout=.002)
    Device(transport)
    assert len(backend.handle.packets)==2 and backend.handle.packets[0]==backend.handle.packets[1]


def test_usb_timeout_and_failed_setup_cleanup():
    backend=Backend();backend.handle.drop=10
    transport=USBTransport('usb:TEST',backend=backend,timeout=.001,retries=2)
    with pytest.raises(TimeoutError,match='USB SLAVE'):Device(transport)
    assert len(backend.handle.packets)==2
    backend=Backend()
    def fail(**kwargs):raise D2XXError('bad mode')
    backend.handle.configure_fifo=fail
    with pytest.raises(D2XXError,match='bad mode'):USBTransport('usb:TEST',backend=backend)
    assert backend.handle.closed


@pytest.mark.parametrize('port',['COM4','usb:','demo',None])
def test_usb_invalid_identifier_before_open(port):
    backend=Backend()
    with pytest.raises(ValueError):USBTransport(port,backend=backend)
    assert backend.serial is None


class BatchSimulator:
    """Translation validates host format; actual atomic RTL checked separately."""
    def __init__(self,batch=True,version=1,size=3,unsupported_storage=False):
        self.sim=ParallelSimulator(4,2,10);self.batch=batch;self.version=version;self.size=size
        self.frames=[];self.unsupported_storage=unsupported_storage
    def exchange(self,frame):
        self.frames.append(frame)
        cmd=frame[3];sequence=int.from_bytes(frame[4:6],'little')
        if cmd==RUN_BATCH:
            assert self.batch
            payload=frame[6:30];start,count,reserved=struct.unpack_from('<HBB',payload)
            assert reserved==0 and 1<=count<=3 and payload[4+6*count:]==bytes(20-6*count)
            for offset in range(count):
                code,ticks=struct.unpack_from('<HI',payload,4+6*offset)
                reply=self.sim.exchange(encode(RUN,(sequence*3+offset)&65535,struct.pack('<HHI',start+offset,code,ticks)))
                assert reply[6]==0
            return encode(cmd|0x80,sequence,b'\0')
        reply=self.sim.exchange(frame)
        if cmd==INFO:
            p=bytearray(reply[6:30]);p[19]|=(2 if self.batch else 0)|(8 if self.unsupported_storage else 0)
            p[22]=self.version;p[23]=self.size
            return encode(cmd|0x80,sequence,p)
        return reply


@pytest.mark.parametrize('batch',[False,True])
def test_batch_negotiation_upload_and_legacy_fallback(batch):
    sim=BatchSimulator(batch);device=Device(sim)
    p=Profile(mode='ramp',initial=10,safe=10,amplitude=0,rise_ms=.001,hold_ms=.001,fall_ms=.001)
    device.upload(p)
    runs=[f for f in sim.frames if f[3] in (RUN,RUN_BATCH)]
    assert runs and all(f[3]==(RUN_BATCH if batch else RUN) for f in runs)
    actual=sim.sim.channels[0].active['segments']
    assert len(runs)==(len(actual)+2)//3 if batch else len(runs)==len(actual)
    assert device.caps.run_batch==batch


@pytest.mark.parametrize('version,size',[(2,3),(1,0),(1,4)])
def test_unknown_batch_contract_rejected_before_mutation(version,size):
    sim=BatchSimulator(version=version,size=size)
    with pytest.raises(ValueError,match='批量'):Device(sim)
    assert [f[3] for f in sim.frames]==[INFO]


def test_legacy_capability_defaults_are_backward_compatible():
    caps=Capabilities()
    assert not caps.run_batch and not caps.usb_fifo
    assert caps.interpolation_min_ticks==32


def test_usb_window_partial_writes_crc_noise_and_sequence_wrap():
    backend=Backend();transport=USBTransport('usb:TEST',backend=backend)
    packets=[encode(INFO,(65530+i)&65535) for i in range(16)]
    replies=transport.exchange_many(packets)
    assert len(replies)==16 and backend.handle.packets==packets
    assert [r[4:6] for r in replies]==[p[4:6] for p in packets]
    assert not backend.handle.partial


def test_usb_window_missing_reply_is_never_retried():
    backend=Backend();backend.handle.drop=1
    transport=USBTransport('usb:TEST',backend=backend,timeout=.005,retries=3)
    packets=[encode(INFO,i+1) for i in range(16)]
    with pytest.raises(TimeoutError,match='不会自动重发'):
        transport.exchange_many(packets)
    assert backend.handle.packets==packets


@pytest.mark.parametrize('packets',[[],[encode(INFO,1)]*2,[encode(INFO,i) for i in range(17)],[b'bad']])
def test_usb_window_invalid_requests_rejected_without_write(packets):
    backend=Backend();transport=USBTransport('usb:TEST',backend=backend)
    with pytest.raises(ValueError):transport.exchange_many(packets)
    assert not backend.handle.packets


def test_device_usb_batch_window_upload_and_failed_window_never_commits():
    backend=Backend();backend.handle.sim=BatchSimulator();backend.handle.noise=False
    transport=USBTransport('usb:TEST',backend=backend);device=Device(transport)
    windows=[];exchange_many=transport.exchange_many
    def traced(packets):
        windows.append(tuple(packets));return exchange_many(packets)
    transport.exchange_many=traced
    p=Profile(mode='ramp',initial=70,safe=70,amplitude=0,rise_ms=.001,hold_ms=.001,fall_ms=.001)
    identity=device.upload(p)
    assert device.status()['config_id']==identity
    assert windows and len(windows[0])==16 and all(len(w)<=16 for w in windows)
    assert all(f[3]==RUN_BATCH for w in windows for f in w)
    backend.handle.packets.clear()
    def missing(packets):raise TimeoutError('missing upload reply')
    transport.exchange_many=missing
    with pytest.raises(TimeoutError,match='missing'):device.upload(p)
    assert not any(f[3]==COMMIT for f in backend.handle.packets)
    assert device.status()['config_id']==identity


def test_unsupported_storage_rejected_without_probe_and_capability_can_recover():
    sim=BatchSimulator();device=Device(sim);previous=device.caps
    sim.unsupported_storage=True;sim.frames.clear()
    with pytest.raises(ValueError):device.refresh_capabilities()
    assert device.caps==previous
    assert [f[3] for f in sim.frames]==[INFO]
    sim.unsupported_storage=False;assert device.refresh_capabilities()==previous


def test_bram_static_stays_one_tick_and_does_not_query_external_memory():
    sim=BatchSimulator();device=Device(sim)
    result=device.set_static(2,123,512)
    run=next(f for f in sim.frames if f[3]==RUN)
    assert struct.unpack_from('<HHI',run,6)==(0,123,1)
    assert result['status']['code']==123
    assert not any(f[3]==0x03 for f in sim.frames)


def test_missing_driver_is_actionable_and_a_later_connection_can_recover(monkeypatch):
    import host.usb_transport as usb
    def missing(*args,**kwargs):raise OSError('driver absent')
    with monkeypatch.context() as patch:
        patch.setattr(usb.os,'name','nt')
        patch.setattr(usb.ctypes,'WinDLL',missing,raising=False)
        with pytest.raises(D2XXError,match='D2XX 驱动'):USBTransport('usb:TEST')
    backend=Backend();transport=USBTransport('usb:TEST',backend=backend)
    assert Device(transport).caps.dac_count==4
    transport.close();assert backend.handle.closed


def test_usb_device_error_in_window_blocks_commit():
    backend=Backend();backend.handle.sim=BatchSimulator();backend.handle.noise=False
    transport=USBTransport('usb:TEST',backend=backend);device=Device(transport)
    exchange_many=transport.exchange_many
    def fail_one(packets):
        replies=exchange_many(packets)
        replies[0]=encode(RUN_BATCH|0x80,int.from_bytes(packets[0][4:6],'little'),b'\x03')
        return replies
    transport.exchange_many=fail_one
    p=Profile(mode='ramp',initial=70,safe=70,amplitude=0,rise_ms=.001,hold_ms=.001,fall_ms=.001)
    with pytest.raises(RuntimeError,match='参数超出范围'):device.upload(p)
    assert not any(f[3]==COMMIT for f in backend.handle.packets)
