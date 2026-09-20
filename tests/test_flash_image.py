import hashlib
import json
import struct
import sys
from pathlib import Path
import pytest
from host.flash import FlashSettings
from host.flash_image import inspect_image, configuration_idcode
from host.flash_worker import prepare_input


def boot_bytes(idcode=0x0362D093, frame=None):
    frame=frame or [0x12345678,0x30018001,0x03631093,0xaa995566]
    # Include false IDCODE/SYNC patterns inside FDRI data: they must be skipped.
    words=[0xffffffff,0x000000bb,0x11220044,0xffffffff,0xaa995566,
           0x20000000,0x30018001,idcode,0x30004000,0x50000000|len(frame),
           *frame,0x30008001,13,0x20000000]
    return struct.pack('>'+str(len(words))+'I',*words)


def hex_record(kind,address=0,payload=b''):
    raw=bytes([len(payload)])+address.to_bytes(2,'big')+bytes([kind])+payload
    return ':'+(raw+bytes([-sum(raw)&255])).hex().upper()


def mcs_bytes(data):
    lines=[]
    for start in range(0,len(data),16):
        if start%65536==0:lines.append(hex_record(4,payload=(start>>16).to_bytes(2,'big')))
        lines.append(hex_record(0,start&65535,data[start:start+16]))
    return '\n'.join(lines+[hex_record(1)])+'\n'


@pytest.mark.parametrize('suffix',['.bin','.BIN','.mcs','.MCS'])
def test_existing_images_match_exact_payload_and_snapshot(tmp_path,suffix):
    data=boot_bytes()+b'\xff'*65536
    src=tmp_path/('原镜像 with spaces'+suffix)
    src.write_bytes(data if suffix.lower()=='.bin' else mcs_bytes(data).encode('ascii'))
    cfg=FlashSettings(image_source='image',image_file=str(src),size_mib=1,target='jtag',device='fpga',cfgmem_part='flash')
    cfg.validate('program')
    image=inspect_image(src,1)
    assert image.data==data and image.idcode==0x0362d093
    out=tmp_path/'job';out.mkdir()
    metadata,info=prepare_input(cfg,'program',out)
    assert 'image_idcode' in metadata and 'bit_part' not in metadata
    assert (out/'firmware.bin').read_bytes()==data
    assert info['payload_sha256']==hashlib.sha256(data).hexdigest()
    src.write_bytes(b'changed source')
    assert Path(cfg.image_file).read_bytes()!=src.read_bytes()
    assert inspect_image(cfg.image_file,1).data==data


@pytest.mark.parametrize('content,match',[
    ('','为空'),('hello','格式'),(':00000001FE\n','校验'),
    (hex_record(4,payload=b'\0\0')+'\n','EOF'),
    (hex_record(1)+'\n'+hex_record(0,payload=b'xxxx'),'EOF 后'),
    (hex_record(0,16,b'xxxx')+'\n'+hex_record(1),'连续'),
    (hex_record(0,0,b'xxxx')+'\n'+hex_record(0,0,b'yyyy')+'\n'+hex_record(1),'连续'),
    (hex_record(4,payload=b'\0\x10')+'\n'+hex_record(0,payload=b'xxxx')+'\n'+hex_record(1),'容量'),
    (hex_record(4,1,b'\0\0')+'\n'+hex_record(1),'扩展地址'),
    (hex_record(0,65535,b'xx')+'\n'+hex_record(1),'64 KiB'),
    (hex_record(5,payload=b'\0'*4)+'\n'+hex_record(1),'不支持记录'),
    (hex_record(1,1)+'\n','EOF 记录'),
])
def test_mcs_invalid_records_rejected(tmp_path,content,match):
    p=tmp_path/'bad.mcs';p.write_text(content,encoding='ascii')
    with pytest.raises(ValueError,match=match):inspect_image(p,1)


def test_segment_address_mcs_and_capacity_boundary(tmp_path):
    data=boot_bytes()+b'\xff'*(1024*1024-len(boot_bytes()))
    p=tmp_path/'whole_flash.bin';p.write_bytes(data)
    assert len(inspect_image(p,1).data)==1024*1024
    p.write_bytes(data+b'\xff')
    with pytest.raises(ValueError,match='容量'):inspect_image(p,1)
    p=tmp_path/'segmented.mcs'
    p.write_text(hex_record(2,payload=b'\0\0')+'\n'+hex_record(0,payload=boot_bytes())+'\n'+hex_record(1),encoding='ascii')
    assert inspect_image(p,1).data==boot_bytes()


@pytest.mark.parametrize('data',[
    b'random raw file',boot_bytes()[:32],boot_bytes()[:-8],
    boot_bytes(idcode=0x12345678),
    boot_bytes().replace(struct.pack('>I',0x50000004),struct.pack('>I',0x57ffffff)),
    boot_bytes().replace(struct.pack('>I',0x30018001),struct.pack('>I',0x30000001),1),
])
def test_invalid_or_truncated_configuration_rejected(tmp_path,data):
    p=tmp_path/'bad.bin';p.write_bytes(data)
    with pytest.raises(ValueError):inspect_image(p,16)


def test_worker_inspect_has_no_vivado_or_hardware_dependency(tmp_path,monkeypatch):
    import host.flash_worker as worker
    p=tmp_path/'image.bin';p.write_bytes(boot_bytes())
    cfg=FlashSettings(image_source='image',image_file=str(p))
    job=tmp_path/'job.json';job.write_text(json.dumps({'settings':cfg.to_dict(),'operation':'inspect','vivado':'nonexistent'}))
    monkeypatch.setattr(sys,'argv',['flash_worker','--job',str(job)])
    monkeypatch.setattr(worker.subprocess,'Popen',lambda *a,**k:pytest.fail('offline inspection must never launch Vivado'))
    assert worker.main()==0
    assert json.loads((tmp_path/'result.json').read_text())['passed']


def test_worker_rejects_bad_image_before_external_process(tmp_path,monkeypatch):
    import host.flash_worker as worker
    p=tmp_path/'image.bin';p.write_bytes(b'bad')
    cfg=FlashSettings(image_source='image',image_file=str(p),cfgmem_part='flash',target='target',device='device')
    job=tmp_path/'job.json';job.write_text(json.dumps({'settings':cfg.to_dict(),'operation':'program','vivado':'nonexistent'}))
    monkeypatch.setattr(sys,'argv',['flash_worker','--job',str(job)])
    monkeypatch.setattr(worker.subprocess,'Popen',lambda *a,**k:pytest.fail('bad image reached external process'))
    with pytest.raises(ValueError):worker.main()


def test_legacy_settings_and_image_operation_separation(tmp_path):
    assert FlashSettings.from_dict({'bitfile':'old.bit'}).image_source=='bit'
    cfg=FlashSettings(image_source='image')
    for op in ('generate','temporary','inspect','program'):
        with pytest.raises(ValueError):cfg.validate(op)
    cfg.image_source='unknown'
    with pytest.raises(ValueError):cfg.validate()
