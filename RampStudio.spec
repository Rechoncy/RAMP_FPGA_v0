# Build with .venv/Scripts/python.exe -m PyInstaller --noconfirm RampStudio.spec
from pathlib import Path
import os
import sys
root = Path(SPECPATH)
# Do not discover DLLs in Conda, image/PDF tools, or another Qt installation.
os.environ['PATH'] = os.pathsep.join([str(Path(sys.executable).parent),sys.base_prefix,
                                    str(Path(sys.base_prefix)/'DLLs'),
                                    str(Path(os.environ['SystemRoot'])/'System32'),os.environ['SystemRoot']])
qt = root/'.venv/Lib/site-packages/PySide6'
runtime_binaries = [(str(qt/name),'.') for name in
                    ('VCRUNTIME140.dll','VCRUNTIME140_1.dll','MSVCP140.dll','MSVCP140_1.dll','MSVCP140_2.dll')]
datas = [(str(root/'host/assets'),'host/assets'),
         (str(root/'rtl'),'rtl'),
         (str(root/'scripts/vivado'),'scripts/vivado'),
         (str(root/'packaging/licenses'),'licenses')]
a = Analysis([str(root/'run_studio.py')], pathex=[str(root)], binaries=runtime_binaries, datas=datas,
             hiddenimports=['PySide6.QtSvg','serial.tools.list_ports_windows'],
             hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['tkinter','pytest','unittest','PySide6.QtTest'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,a.binaries,a.datas,[],name='Ramp Studio',debug=False,
          bootloader_ignore_signals=False,strip=False,upx=False,console=False,
          disable_windowed_traceback=False,icon=str(root/'host/assets/ramp-studio.ico'))
