"""Persistent SPI boot settings and safely encoded Vivado job configuration."""
from dataclasses import dataclass,asdict
from pathlib import Path
import re


@dataclass
class FlashSettings:
    cfgmem_part: str = ''
    size_mib: int = 16
    interface: str = 'SPIx1'
    config_rate: int = 16
    falling_edge: bool = True
    server: str = 'localhost:3121'
    target: str = ''
    device: str = ''
    bitfile: str = ''
    expected_usr_access: str = ''
    backup: bool = True

    def to_dict(self):return asdict(self)

    @classmethod
    def from_dict(cls,data):
        if not isinstance(data,dict):raise ValueError('Flash 设置必须是对象')
        return cls(**data)

    def validate(self,operation='settings'):
        if self.interface not in ('SPIx1','SPIx2','SPIx4'):raise ValueError('Flash 接口须为 SPIx1 / SPIx2 / SPIx4')
        if type(self.size_mib) is not int or self.size_mib not in (1,2,4,8,16,32,64,128,256):raise ValueError('Flash 容量须为 1～256 MiB 的 2 次幂；128 Mbit = 16 MiB')
        if type(self.config_rate) is not int or self.config_rate not in (3,6,9,12,16,22,26,33,40,50,66):raise ValueError('不支持的 Flash 配置时钟')
        if type(self.backup) is not bool or type(self.falling_edge) is not bool:raise ValueError('Flash 开关设置无效')
        for name,value in self.to_dict().items():
            if isinstance(value,str) and any(c in value for c in '\r\n\x00'):raise ValueError(f'{name} 含非法控制字符')
        if self.expected_usr_access and not re.fullmatch(r'(?:0x)?[0-9a-fA-F]{8}',self.expected_usr_access):raise ValueError('固件标识须为 8 位十六进制，或留空只检查 DONE / CRC')
        if operation in ('generate','program','temporary'):
            bit=Path(self.bitfile)
            if not bit.is_file() or bit.suffix.lower()!='.bit':raise ValueError('请选择存在的 .bit 文件')
            if bit.stat().st_size>=self.size_mib*1024*1024:raise ValueError('比特流超过所选 Flash 容量')
        if operation in ('program','temporary','boot','status') and (not self.target or not self.device):raise ValueError('请扫描 JTAG 并明确选择目标与 FPGA 器件')
        if operation=='program' and not re.fullmatch(r'[a-zA-Z0-9_.-]+',self.cfgmem_part):raise ValueError('请填写 Vivado 的完整 Flash 型号')


def tcl_word(value):
    # Double-quoted Tcl words: escape every substitution and delimiter.
    text=str(value)
    if any(c in text for c in '\r\n\x00'):raise ValueError('Tcl 参数含非法控制字符')
    return '"'+''.join('\\'+c if c in '\\"$[]' else c for c in text)+'"'


def job_config(settings,operation,output):
    settings.validate(operation)
    values=settings.to_dict()|{'operation':operation,'output':Path(output).resolve().as_posix()}
    if settings.bitfile:values['bitfile']=Path(settings.bitfile).resolve().as_posix()
    return '\n'.join('set cfg('+key+') '+tcl_word(int(value) if isinstance(value,bool) else value) for key,value in values.items())+'\n'


def bitstream_part(path):
    """Read the Xilinx .bit header's target part before allowing a hardware write."""
    with Path(path).open('rb') as f:
        def word():
            raw=f.read(2)
            if len(raw)!=2:raise ValueError('比特流头不完整')
            return int.from_bytes(raw,'big')
        length=word()
        if length!=9 or f.read(length)!=bytes.fromhex('0ff00ff00ff00ff000'):raise ValueError('不是有效的 Xilinx .bit 文件')
        if word()!=1:raise ValueError('比特流头格式无效')
        for tag in (b'a',b'b',b'c',b'd'):
            if f.read(1)!=tag:raise ValueError('比特流头缺少型号信息')
            value=f.read(word())
            if tag==b'b':
                part=value.rstrip(b'\0').decode('ascii').lower()
                if not re.fullmatch(r'(?:xc)?7[a-z0-9]+',part):raise ValueError('仅支持 Xilinx 7 系列比特流')
                return part.removeprefix('xc')
    raise ValueError('无法读取比特流 FPGA 型号')
