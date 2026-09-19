"""Refresh release documentation and existing portable ZIP without rebuilding EXE."""
from pathlib import Path
import hashlib
import re
import shutil
from urllib.parse import unquote, urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DOCS, RELEASE = ROOT / "docs", ROOT / "release"


def main():
    RELEASE.mkdir(exist_ok=True)
    shutil.copytree(DOCS, RELEASE / "文档", dirs_exist_ok=True)
    shutil.copy2(ROOT / 'packaging' / '发布目录说明.md', RELEASE / 'README.md')
    migration_source = DOCS / '迁移到其他电脑.html'
    migration_entry = RELEASE / '迁移说明.html'
    if migration_source.is_file():
        def migration_link(match):
            link = match.group(1)
            if link.startswith(('#', '/')) or urlsplit(link).scheme:
                return match.group(0)
            return f'href="文档/{link}"'
        migration_entry.write_text(re.sub(r'href="([^"]+)"', migration_link,
            migration_source.read_text(encoding='utf-8')), encoding='utf-8')
    examples = ROOT / 'examples' / 'sequences'
    if examples.exists():
        shutil.copytree(examples, RELEASE / '序列示例', dirs_exist_ok=True)
    scans=ROOT/'examples'/'scans'
    if scans.exists():shutil.copytree(scans,RELEASE/'扫描示例',dirs_exist_ok=True)
    interpolation=ROOT/'examples'/'interpolation'
    if interpolation.exists():shutil.copytree(interpolation,RELEASE/'插值示例',dirs_exist_ok=True)
    boards=RELEASE/'板卡示例'
    boards.mkdir(exist_ok=True)
    for name in ('xc7a35t_four_dac.json','xc7a35t_usb_uart.json','xc7a35t_usb_uart_diagnostic.json','davinci_3pd5651e_125msps.json'):
        shutil.copy2(ROOT/'boards'/name,boards/name)
    # Source-tree examples and portable examples have different folder names.
    for extension in ('md','html'):
        guide=RELEASE/'文档'/f'14位高斯分段插值.{extension}'
        if guide.is_file():
            guide.write_text(guide.read_text(encoding='utf-8').replace(
                '../examples/interpolation/','../插值示例/'),encoding='utf-8')
    # Keep the existing top-level quick guides in sync as well.
    for src in DOCS.glob("*.md"):
        target = RELEASE / ("使用说明.md" if src.name == "Windows便携版说明.md" else src.name)
        if target.exists():
            shutil.copy2(src, target)
            if target.name == '使用说明.md':
                def quick_guide_link(match):
                    link = match.group(1)
                    parsed = urlsplit(link)
                    if (not parsed.scheme and not link.startswith(('#', '/'))
                            and (DOCS / unquote(parsed.path)).is_file()):
                        return f'](文档/{link})'
                    return match.group(0)
                target.write_text(re.sub(r'\]\(([^)\s]+)\)', quick_guide_link,
                    src.read_text(encoding='utf-8')), encoding='utf-8')
    standalone = RELEASE / "Ramp-Studio-Documentation.zip"
    docs_files = sorted(p for p in (RELEASE / "文档").rglob("*") if p.is_file())
    example_files = sorted(p for folder in ('序列示例', '扫描示例', '插值示例', '板卡示例')
                           for p in (RELEASE / folder).rglob('*') if p.is_file())
    temp = standalone.with_suffix(".tmp.zip")
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as archive:
        # Offline manuals link to sibling example directories. Keep those
        # targets in the standalone documentation ZIP as in the portable ZIP.
        for p in [*docs_files, *example_files]:
            archive.write(p, p.relative_to(RELEASE).as_posix())
    with zipfile.ZipFile(temp) as archive:
        assert archive.testzip() is None
    temp.replace(standalone)

    portable = RELEASE / "Ramp-Studio-Windows-x64.zip"
    if portable.exists():
        temp = portable.with_suffix(".tmp.zip")
        with zipfile.ZipFile(portable) as source, zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as target:
            for entry in source.infolist():
                normalized = entry.filename.replace("\\", "/")
                if normalized.startswith(("文档/", "序列示例/", "扫描示例/", "插值示例/", "板卡示例/")):
                    continue
                if normalized == '迁移说明.html':
                    continue
                # Keep one manual collection instead of duplicate topical files.
                if '/' not in normalized and normalized.endswith('.md') and normalized not in ('使用说明.md',):
                    continue
                local = RELEASE / normalized
                if "/" not in normalized and normalized.endswith(".md") and local.is_file():
                    target.writestr(entry, local.read_bytes())
                else:
                    target.writestr(entry, source.read(entry))
            for p in docs_files:
                target.write(p, p.relative_to(RELEASE).as_posix())
            target.write(RELEASE / 'README.md', 'README.md')
            if migration_entry.is_file():
                target.write(migration_entry, migration_entry.name)
            for p in example_files:
                target.write(p, p.relative_to(RELEASE).as_posix())
        with zipfile.ZipFile(temp) as archive:
            assert archive.testzip() is None
            assert len(archive.namelist()) == len(set(archive.namelist()))
        temp.replace(portable)

    artifacts = [RELEASE / "Ramp Studio.exe", *sorted(RELEASE.glob("*.zip"))]
    hashes = [f"{hashlib.file_digest(p.open('rb'), 'sha256').hexdigest()}  {p.name}" for p in artifacts if p.is_file()]
    (RELEASE / "SHA256.txt").write_text("\n".join(hashes) + "\n", encoding="utf-8")
    print(f"Packaged {len(docs_files)} documentation files and {len(example_files)} examples; executable and FPGA archives unchanged.")


if __name__ == "__main__":
    main()
