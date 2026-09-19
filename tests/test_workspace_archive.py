"""Configuration-only transfer, hostile input rejection and rollback behavior."""
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import zipfile

import pytest

from host.board import Board
from host.model import Profile
from host import workspace_archive as wa


def raw(data):
    return json.dumps(data, ensure_ascii=False).encode('utf-8')


def put(root, name, value):
    path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else raw(value))
    return path


def fixture(root, *, name='工作区 A'):
    p = Profile(mode='ramp', name=name, initial=512, safe=512)
    put(root, f'profiles/{p.id}.json', p.to_dict())
    put(root, 'board.json', Board(name=name, uart_baud=1000000).to_dict())
    put(root, '.sample_profiles_added', b'')
    put(root, 'parallel.json', {'0': {'profile': p.id, 'enabled': True, 'trigger_override': {'trigger_input': 1}}})
    draft = dict(profile=p.to_dict(), texts={'function': 'exp('}, values={'rise_ms': 17.25},
                 units={'rise_ms': 'us'}, combos={'trigger_input': 1}, table=[['01', '', 'x', '']])
    put(root, 'designer_draft.json', dict(selected=0, drafts={'0': draft}))
    put(root, 'desktop_session.json', dict(schema=1, designer=draft, view={'baudrate': 1000000, 'port': 'COM66'},
        board={'schema': 1, 'fields': {'part': '', 'ref': {'value': 100.0, 'text': '1e'}},
               'flash': {'bit': 'Z:/not-present/firmware.bit', 'size': {'value': 16, 'text': '-'}}}))
    put(root, 'flash_selection.json', {'0': True, '1': False})
    return p


def archive(root, target):
    wa.save_archive(root, target)
    return wa.read_archive(target)


def repack(path, mutate):
    with zipfile.ZipFile(path) as z:
        entries = [(i.filename, z.read(i.filename)) for i in z.infolist()]
    entries = mutate(entries)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_STORED) as z:
        for name, content in entries:
            z.writestr(name, content)


def modified_payload(path, name, data):
    def change(entries):
        contents = dict(entries); contents[name] = data
        m = json.loads(contents['manifest.json'])
        for entry in m['files']:
            if entry['path'] == name:
                entry.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
        contents['manifest.json'] = raw(m)
        return list(contents.items())
    repack(path, change)


def test_roundtrip_self_contained_and_excludes_local_artifacts(tmp_path):
    source = tmp_path / '原始设置'; p = fixture(source)
    for name in ('logs/run.jsonl', 'builds/tool.exe', 'flash_jobs/image.bit', 'loaded_waveforms.json',
                 'resource_report_selection.json', 'resource_reports/report.rpt', 'desktop.ini', 'archive/old.json'):
        put(source, name, b'not portable')
    snapshot = archive(source, tmp_path / '实验.ramp-workspace')
    assert snapshot.summary['profiles'] == 1
    assert snapshot.summary['board_name'] == '工作区 A'
    assert snapshot.summary['file_count'] == 7
    assert snapshot.summary['created_at'].endswith('+00:00')
    assert snapshot.files['designer_draft.json'] == (source / 'designer_draft.json').read_bytes()
    assert not any('resource_' in name or 'loaded_' in name or name.endswith('.bit') for name in snapshot.files)
    with zipfile.ZipFile(tmp_path / '实验.ramp-workspace') as z:
        manifest = json.loads(z.read('manifest.json'))
        for entry in manifest['files']:
            assert entry['sha256'] == hashlib.sha256(z.read(entry['path'])).hexdigest()
    destination = tmp_path / '目标'; old = fixture(destination, name='旧方案')
    put(destination, 'sequence_draft.json', Profile(mode='sequence', sequence_steps=[]).to_dict())
    for name in ('loaded_waveforms.json', 'resource_report_selection.json', 'resource_reports/a.rpt', 'logs/keep.jsonl'):
        put(destination, name, b'preserve existing')
    backup = wa.restore_archive(destination, snapshot)
    assert backup.parent == destination / 'archive/workspaces'
    assert wa.read_archive(backup).files[f'profiles/{old.id}.json']
    assert not (destination / f'profiles/{old.id}.json').exists()
    assert not (destination / 'sequence_draft.json').exists()
    assert (destination / f'profiles/{p.id}.json').is_file()
    assert (destination / 'loaded_waveforms.json').read_bytes() == b'preserve existing'
    assert (destination / 'resource_report_selection.json').read_bytes() == b'preserve existing'
    assert (destination / 'resource_reports/a.rpt').read_bytes() == b'preserve existing'
    assert all((destination / name).read_bytes() == content for name, content in snapshot.files.items())
    wa.restore_archive(destination, wa.read_archive(backup))
    assert (destination / f'profiles/{old.id}.json').is_file()
    assert not (destination / f'profiles/{p.id}.json').exists()
    assert (destination / 'sequence_draft.json').is_file()


def test_wave_sequence_scan_snapshots_and_empty_library(tmp_path):
    root = tmp_path / 'source'; p = fixture(root)
    sequence = Profile(name='两个时刻', mode='sequence', initial=512, safe=512,
                       sequence_steps=[dict(waveform=p.to_dict(), start_ms=0, kind='edge', repeat=1)])
    scan = Profile(name='扫描', mode='scan', initial=512, safe=512)
    put(root, f'profiles/{sequence.id}.json', sequence.to_dict())
    put(root, f'profiles/{scan.id}.json', scan.to_dict())
    put(root, 'scan_draft.json', {'profile': dict(scan.to_dict(), scan_points=0), 'saved': '', 'fresh': True})
    snapshot = archive(root, tmp_path / 'complete.ramp-workspace')
    assert (snapshot.summary['waveform_count'], snapshot.summary['sequence_count'], snapshot.summary['scan_count']) == (1, 1, 1)
    for path in (root / 'profiles').glob('*.json'):
        path.unlink()
    empty = archive(root, tmp_path / 'empty-library.ramp-workspace')
    assert empty.summary['profiles'] == 0
    assert len(empty.summary['warnings']) == 1


@pytest.mark.parametrize('bad_name', ['../board.json', '/board.json', 'C:/board.json', 'profiles/../board.json',
                                    'profiles\\bad.json', 'logs/run.jsonl', 'loaded_waveforms.json',
                                    'resource_report_selection.json', 'profiles/UPPER.json', 'board.json:stream'])
def test_disallowed_archive_paths_are_rejected_before_mutation(tmp_path, bad_name):
    source = tmp_path / 'source'; fixture(source)
    path = tmp_path / 'bad.ramp-workspace'; archive(source, path)
    repack(path, lambda entries: entries + [(bad_name, b'{}')])
    before = wa._collect(source)
    with pytest.raises(ValueError):
        wa.read_archive(path)
    assert wa._collect(source) == before
    assert not (source / 'archive/workspaces').exists()


@pytest.mark.parametrize('kind', ['sha', 'size', 'missing', 'extra', 'duplicate', 'schema', 'format', 'timestamp', 'summary_ignored'])
def test_manifest_integrity(tmp_path, kind):
    root = tmp_path / 'source'; fixture(root)
    path = tmp_path / 'bad.ramp-workspace'; archive(root, path)
    def change(entries):
        contents = dict(entries); manifest = json.loads(contents['manifest.json'])
        if kind == 'sha': manifest['files'][0]['sha256'] = '0' * 64
        elif kind == 'size': manifest['files'][0]['size'] += 1
        elif kind == 'missing': return entries[:-1]
        elif kind == 'extra': manifest['files'].append(manifest['files'][0])
        elif kind == 'duplicate': return entries + [entries[0]]
        elif kind == 'schema': manifest['schema'] = 99
        elif kind == 'format': manifest['format'] = 'unknown'
        elif kind == 'timestamp': manifest['created_at'] = '2026-09-15T12:00:00'
        elif kind == 'summary_ignored': manifest['summary']['profiles'] = 9999
        contents['manifest.json'] = raw(manifest)
        return list(contents.items())
    repack(path, change)
    if kind == 'summary_ignored':
        assert wa.read_archive(path).summary['profiles'] == 1
    else:
        with pytest.raises(ValueError):
            wa.read_archive(path)


@pytest.mark.parametrize('payload', [b'{', b'[]', b'{"schema":1,"schema":1}',
                                    b'{"schema":1,"view":{"baudrate":NaN}}',
                                    b'{"schema":1,"view":{"baudrate":true}}',
                                    b'{"schema":1,"view":{"page":"zero"}}',
                                    b'{"schema":1,"assignments":{"99":{}}}',
                                    b'{"schema":1,"designer":{"profile":[]}}'])
def test_malformed_json_and_session_rejected(tmp_path, payload):
    root = tmp_path / 'source'; fixture(root)
    path = tmp_path / 'bad.ramp-workspace'; archive(root, path)
    modified_payload(path, 'desktop_session.json', payload)
    with pytest.raises(ValueError):
        wa.read_archive(path)


def test_no_external_path_read_or_expression_evaluation(tmp_path, monkeypatch):
    from host import functions
    root = tmp_path / 'source'; fixture(root)
    function = Profile(mode='function', name='高斯', initial=512, safe=512)
    put(root, f'profiles/{function.id}.json', function.to_dict())
    put(root, 'board.json', Board(boot_profiles={'0': function.to_dict()}).to_dict())
    def forbidden(*args, **kwargs):
        pytest.fail('Archive validation must not evaluate expressions')
    monkeypatch.setattr(functions, 'evaluate', forbidden)
    archive(root, tmp_path / 'safe.ramp-workspace')
    malicious = function.to_dict() | {'rise_function': '__import__("os").system("calc")'}
    put(root, f'profiles/{function.id}.json', malicious)
    with pytest.raises(ValueError):
        wa.save_archive(root, tmp_path / 'rejected.ramp-workspace')


@pytest.mark.parametrize('unit', ['ns', 'us', 'µs', 'μs', 'ms', 's'])
def test_current_and_legacy_microsecond_spellings(tmp_path, unit):
    root = tmp_path / 'source'; fixture(root)
    data = json.loads((root / 'designer_draft.json').read_bytes())
    data['drafts']['0']['units']['rise_ms'] = unit
    put(root, 'designer_draft.json', data)
    snapshot = archive(root, tmp_path / 'units.ramp-workspace')
    assert json.loads(snapshot.files['designer_draft.json'])['drafts']['0']['units']['rise_ms'] == unit


def test_damaged_deflate_stream_raises_friendly_value_error(tmp_path):
    root = tmp_path / 'source'; fixture(root)
    path = tmp_path / 'bad.ramp-workspace'; archive(root, path)
    with zipfile.ZipFile(path) as z:
        info = z.getinfo('manifest.json')
        offset = info.header_offset
    content = bytearray(path.read_bytes())
    name_len = int.from_bytes(content[offset + 26:offset + 28], 'little')
    extra_len = int.from_bytes(content[offset + 28:offset + 30], 'little')
    content[offset + 30 + name_len + extra_len] = 0x07  # Invalid DEFLATE block type.
    path.write_bytes(content)
    with pytest.raises(ValueError, match='归档损坏'):
        wa.read_archive(path)


def test_limits_zip_symlinks_and_crc(tmp_path, monkeypatch):
    root = tmp_path / 'source'; fixture(root)
    path = tmp_path / 'bad.ramp-workspace'; archive(root, path)
    with monkeypatch.context() as context:
        context.setattr(wa, 'MAX_ARCHIVE_BYTES', 1)
        with pytest.raises(ValueError): wa.read_archive(path)
    with monkeypatch.context() as context:
        context.setattr(wa, 'MAX_FILE_BYTES', 1)
        with pytest.raises(ValueError): wa.read_archive(path)
    with monkeypatch.context() as context:
        context.setattr(wa, 'MAX_FILES', 1)
        with pytest.raises(ValueError): wa.read_archive(path)
    with zipfile.ZipFile(path) as z:
        contents = [(i.filename, z.read(i.filename)) for i in z.infolist()]
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_STORED) as z:
        for name, content in contents:
            info = zipfile.ZipInfo(name); info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16 if name == 'board.json' else (stat.S_IFREG | 0o644) << 16
            z.writestr(info, content)
    with pytest.raises(ValueError): wa.read_archive(path)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_STORED) as z:
        for name, content in contents: z.writestr(name, content)
    content = path.read_bytes(); marker = b'Artix-7'
    # Alter a stored, non-manifest member without updating its CRC.
    board_raw = dict(contents)['board.json']; at = content.find(board_raw)
    assert at >= 0
    content = content[:at + 1] + bytes([content[at + 1] ^ 1]) + content[at + 2:]
    path.write_bytes(content)
    with pytest.raises(ValueError): wa.read_archive(path)


def test_failed_replace_rolls_back_exact_bytes_and_keeps_recovery(tmp_path, monkeypatch):
    source = tmp_path / 'source'; fixture(source)
    snapshot = archive(source, tmp_path / 'in.ramp-workspace')
    destination = tmp_path / 'destination'; fixture(destination, name='原始工作区')
    before = wa._collect(destination)
    original = wa.os.replace; failed = False
    def fail_once(src, dst):
        nonlocal failed
        if '.workspace-import-' in str(src) and Path(dst).name == 'designer_draft.json' and not failed:
            failed = True
            raise PermissionError('simulated locked file')
        return original(src, dst)
    monkeypatch.setattr(wa.os, 'replace', fail_once)
    with pytest.raises(OSError, match='原配置已恢复'):
        wa.restore_archive(destination, snapshot)
    assert failed and wa._collect(destination) == before
    backups = list((destination / 'archive/workspaces').glob('*.ramp-workspace'))
    assert len(backups) == 1 and wa.read_archive(backups[0]).files == before
    assert not list(destination.glob('.workspace-import-*'))


def test_cannot_overwrite_own_config_or_mutate_snapshot_paths(tmp_path):
    root = tmp_path / 'source'; p = fixture(root)
    before = wa._collect(root)
    for target in (root / 'board.json', root / f'profiles/{p.id}.json', root / 'profiles/archive.ramp-workspace'):
        with pytest.raises(ValueError): wa.save_archive(root, target)
    snapshot = archive(root, tmp_path / 'valid.ramp-workspace')
    snapshot.files['../outside.json'] = b'{}'
    with pytest.raises(ValueError): wa.restore_archive(root, snapshot)
    assert wa._collect(root) == before and not (root / 'archive/workspaces').exists()


@pytest.mark.parametrize('location', ['root', 'profiles', 'profile', 'archive'])
def test_detected_link_rejected_before_read_or_write(tmp_path, monkeypatch, location):
    """Exercise the rejection policy without requiring OS link-creation rights.

    Only link detection is simulated. Archive collection, validation and save
    remain real; attempts to read the protected configuration fail the test.
    Native symlinks and NTFS junctions have separate integration tests below.
    """
    root = tmp_path / 'source'; p = fixture(root)
    profile = root / f'profiles/{p.id}.json'
    target = tmp_path / 'unsafe.ramp-workspace'
    paths = dict(root=root, profiles=root / 'profiles', profile=profile, archive=target)
    linked = paths[location]
    before = wa._collect(root)
    is_symlink = Path.is_symlink
    read_bytes = Path.read_bytes

    def detect(path):
        return path == linked or is_symlink(path)

    def guarded_read(path):
        if location != 'archive' and path == profile:
            pytest.fail('Archive export read the configuration behind a detected link')
        return read_bytes(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'is_symlink', detect)
        patch.setattr(Path, 'read_bytes', guarded_read)
        with pytest.raises(ValueError, match='链接'):
            wa.save_archive(root, target)
    assert not target.exists()
    assert wa._collect(root) == before


def test_linked_profile_path_rejected_without_following(tmp_path, monkeypatch):
    """Native symlink integration; do not replace this with a simulated pass."""
    root = tmp_path / 'source'; p = fixture(root)
    external = tmp_path / 'external.json'; external.write_bytes((root / 'board.json').read_bytes())
    before = external.read_bytes()
    linked = root / f'profiles/{p.id}.json'; linked.unlink()
    try:
        linked.symlink_to(external)
    except OSError as exc:
        if os.name == 'nt' and exc.winerror == 1314:
            pytest.skip('Native symlink integration requires Windows link-creation privilege '
                        '(winerror=1314); detection-policy and native junction tests run separately')
        raise
    read_bytes = Path.read_bytes

    def guarded_read(path):
        if path in (linked, external):
            pytest.fail('Archive export read through a native symbolic link')
        return read_bytes(path)

    target = tmp_path / 'unsafe.ramp-workspace'
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'read_bytes', guarded_read)
        with pytest.raises(ValueError, match='链接'):
            wa.save_archive(root, target)
    assert not target.exists()
    assert external.read_bytes() == before


@pytest.mark.parametrize('location', ['root', 'profiles'])
def test_native_windows_junction_rejected_without_following(tmp_path, monkeypatch, location):
    """NTFS directory junctions are real reparse points, not symbolic links."""
    if os.name != 'nt':
        pytest.skip('Native NTFS junction integration is Windows-only')
    import _winapi

    external = tmp_path / 'external'; fixture(external)
    before = wa._collect(external)
    root = tmp_path / 'source'
    if location == 'root':
        linked, destination = root, external
    else:
        put(root, 'board.json', Board().to_dict())
        linked, destination = root / 'profiles', external / 'profiles'
    _winapi.CreateJunction(str(destination), str(linked))
    read_bytes = Path.read_bytes

    def guarded_read(path):
        if path.is_relative_to(external) or path.is_relative_to(linked):
            pytest.fail('Archive export read through a native NTFS junction')
        return read_bytes(path)

    target = tmp_path / 'unsafe.ramp-workspace'
    try:
        assert linked.is_junction()
        with monkeypatch.context() as patch:
            patch.setattr(Path, 'read_bytes', guarded_read)
            with pytest.raises(ValueError, match='联接点'):
                wa.save_archive(root, target)
        assert not target.exists()
        assert wa._collect(external) == before
    finally:
        # Remove the junction itself, never recurse into its destination.
        linked.rmdir()
    assert wa._collect(external) == before
