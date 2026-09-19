"""Collect installed dependency notices for the distributed binary."""
from importlib.metadata import distribution
from pathlib import Path
import shutil
import sys

root=Path(__file__).resolve().parents[1]/'packaging/licenses'
root.mkdir(parents=True,exist_ok=True)
for name in ('PySide6-Essentials','shiboken6','numpy','pyserial','PyInstaller'):
    dist=distribution(name)
    target=root/name
    target.mkdir(exist_ok=True)
    (target/'PACKAGE.txt').write_text(f'{dist.metadata["Name"]} {dist.version}\n{dist.metadata.get("Home-page", "")}\n',encoding='utf-8')
    for item in dist.files or []:
        if any(word in item.name.lower() for word in ('license','copying','copyright','notice')) and str(item).endswith(('.txt','.md','.rst','LICENSE','COPYING','NOTICE')):
            source=Path(dist.locate_file(item))
            if source.is_file():
                destination=target/Path(*[p for p in item.parts if p not in ('..','.')])
                destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(source,destination)
python_license=Path(sys.base_prefix)/'LICENSE.txt'
if python_license.is_file(): shutil.copy2(python_license,root/'Python-LICENSE.txt')
