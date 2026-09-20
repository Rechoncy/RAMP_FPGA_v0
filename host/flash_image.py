"""Inspect address-zero SPI boot images without Vivado or hardware access.

The supported input is the same single-image layout produced by write_cfgmem.
MCS is Intel HEX (data, EOF, extended segment/linear address records). Firmware
IDCODE is read from configuration packets, never searched inside frame payloads.
Packet layout: AMD UG470, chapter 5 (7 Series configuration).
"""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import re
import struct


@dataclass(frozen=True)
class FlashImage:
    data: bytes
    format: str
    idcode: int

    def summary(self):
        return dict(format=self.format, start_address=0, bytes=len(self.data),
                    end_address=len(self.data)-1, idcode=f'{self.idcode:08X}',
                    payload_sha256=hashlib.sha256(self.data).hexdigest())


def read_mcs(path, capacity):
    data = bytearray()
    base = 0
    eof = False
    with Path(path).open('r', encoding='ascii') as stream:
        for line_number, line in enumerate(stream, 1):
            line = line.strip()
            if not line:
                continue
            prefix = f'MCS 第 {line_number} 行：'
            if eof:
                raise ValueError(prefix+'EOF 后还有记录')
            if not re.fullmatch(r':[0-9a-fA-F]{10,520}', line) or len(line) % 2 != 1:
                raise ValueError(prefix+'Intel HEX 记录格式错误')
            record = bytes.fromhex(line[1:])
            count, kind = record[0], record[3]
            address = int.from_bytes(record[1:3], 'big')
            payload = record[4:-1]
            if len(record) != count+5 or sum(record) & 255:
                raise ValueError(prefix+'长度或校验和错误')
            if kind == 0:
                if address+count > 0x10000:
                    raise ValueError(prefix+'数据记录跨越 64 KiB 地址边界')
                start = base+address
                if start+count > capacity:
                    raise ValueError(prefix+'镜像地址超出所选 Flash 容量')
                if start != len(data):
                    raise ValueError(prefix+'仅支持从地址 0 开始、按地址连续排列的单镜像；存在空洞、重叠或非零起始地址')
                data.extend(payload)
            elif kind in (2, 4):
                if count != 2 or address:
                    raise ValueError(prefix+'扩展地址记录错误')
                base = int.from_bytes(payload, 'big') << (4 if kind == 2 else 16)
            elif kind == 1:
                if count or address:
                    raise ValueError(prefix+'EOF 记录错误')
                eof = True
            else:
                raise ValueError(prefix+f'不支持记录类型 {kind:02X}；请选择单镜像 SPI 启动文件')
    if not eof or not data:
        raise ValueError('MCS 缺少 EOF 或没有镜像数据')
    return bytes(data)


def configuration_idcode(data):
    """Validate packet boundaries through DESYNC; skip FDRI/type-2 payloads."""
    sync = data.find(b'\xaa\x99\x55\x66', 0, 4096)
    if sync < 0 or sync % 4:
        raise ValueError('未找到标准 SPI 配置同步字；请选择 Vivado 生成的 7 系列 MCS/BIN（不做字节翻转）')
    pos, register, idcode, frames = sync+4, None, None, False
    while pos+4 <= len(data):
        header = struct.unpack_from('>I', data, pos)[0]
        pos += 4
        kind, opcode = header >> 29, (header >> 27) & 3
        if kind == 1:
            register, count = (header >> 13) & 0x3fff, header & 0x7ff
            if header & 0x1800:
                raise ValueError('配置包保留位不为零')
        elif kind == 2 and register is not None:
            count = header & 0x7ffffff
        else:
            raise ValueError('配置包格式错误；不支持加密或非标准镜像')
        if opcode == 0 and count == 0:
            continue
        if opcode != 2 or pos+count*4 > len(data):
            raise ValueError('配置包被截断或包含不支持的操作')
        if register == 12 and count:
            value = struct.unpack_from('>I', data, pos)[0]
            if count != 1 or value & 0xfff != 0x093 or (idcode is not None and idcode != value):
                raise ValueError('镜像包含无效或不一致的 FPGA IDCODE')
            idcode = value
        if register == 2 and count:
            if idcode is None:
                raise ValueError('配置帧之前缺少 FPGA IDCODE')
            frames = True
        if register == 4 and count == 1 and struct.unpack_from('>I', data, pos)[0] == 13:
            if idcode is None or not frames:
                raise ValueError('镜像缺少 FPGA IDCODE 或配置帧')
            return idcode
        pos += count*4
    raise ValueError('镜像不完整：未找到配置结束命令 DESYNC')


def inspect_image(path, size_mib):
    path = Path(path)
    capacity = size_mib*1024*1024
    if not path.is_file() or path.suffix.lower() not in ('.mcs', '.bin'):
        raise ValueError('请选择存在的 .mcs 或 .bin 文件')
    if not path.stat().st_size:
        raise ValueError('Flash 镜像为空')
    if path.suffix.lower() == '.bin':
        if path.stat().st_size > capacity:
            raise ValueError('BIN 镜像超过所选 Flash 容量')
        data = path.read_bytes()
    else:
        try:
            data = read_mcs(path, capacity)
        except UnicodeError as exc:
            raise ValueError('MCS 必须是 ASCII Intel HEX 文本') from exc
    return FlashImage(data, path.suffix[1:].upper(), configuration_idcode(data))
