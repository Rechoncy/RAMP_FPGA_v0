"""Portable configuration archives; import never opens devices or external paths.

Only the explicitly managed configuration files are replaced. Reports, device
confirmation caches, logs, executables and firmware are deliberately excluded.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timezone
import hashlib
import base64
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import uuid
import zipfile
import zlib

FORMAT = 'ramp-studio-workspace'
SCHEMA = 1
SUFFIX = '.ramp-workspace'
MAX_FILES = 2048
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 72 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MANAGED_FILES = frozenset({
    'board.json', 'desktop_session.json', 'designer_draft.json', 'dac_editors.json',
    'sequence_draft.json', 'scan_draft.json', 'parallel.json', 'flash_selection.json',
    '.sample_profiles_added',
})
_ID = re.compile(r'[0-9a-f]{32}')
_CHANNELS = frozenset(str(n) for n in range(8))
_TIME_UNITS = frozenset(('ns', 'us', 'µs', 'μs', 'ms', 's'))


@dataclass(frozen=True)
class WorkspaceSnapshot:
    files: dict[str, bytes]
    summary: dict


def _fail(message):
    raise ValueError('全局工作区：' + message)


def _object(value, label):
    if not isinstance(value, dict):
        _fail(f'{label} 必须是对象')
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail(f'JSON 重复字段：{key}')
        result[key] = value
    return result


def _json(raw, label):
    try:
        value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_pairs,
                           parse_constant=lambda x: _fail(f'{label} 含非有限数值'))
        pending = [(value, 0)]
        nodes = 0
        while pending:
            item, depth = pending.pop(); nodes += 1
            if depth > 32 or nodes > 100000:
                _fail(f'{label} 结构过于复杂')
            if isinstance(item, dict):
                pending.extend((v, depth + 1) for v in item.values())
            elif isinstance(item, list):
                pending.extend((v, depth + 1) for v in item)
            elif isinstance(item, str) and (len(item) > 100000 or '\x00' in item):
                _fail(f'{label} 文本过长或含空字符')
            elif isinstance(item, float) and not math.isfinite(item):
                _fail(f'{label} 含非有限数值')
        return _object(value, label)
    except (UnicodeError, RecursionError, OverflowError, json.JSONDecodeError) as exc:
        raise ValueError(f'全局工作区：{label} 不是有效的 JSON：{exc}') from exc


def _name(name):
    if not isinstance(name, str) or '\\' in name or ':' in name or '\x00' in name:
        _fail('文件路径无效')
    parts = PurePosixPath(name).parts
    if not parts or PurePosixPath(name).is_absolute() or any(p in ('.', '..') for p in parts):
        _fail('归档包含越界路径')
    if name in MANAGED_FILES:
        return name
    if re.fullmatch(r'profiles/[0-9a-f]{32}\.json', name):
        return name
    _fail(f'不允许导入文件：{name}')


def _integer(value, label, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        _fail(f'{label} 必须是 {lower}～{upper} 的整数')


def _profile(data, label, *, draft=False, depth=0):
    """Check loadable data and expression syntax without evaluating a waveform."""
    from .model import Profile
    _object(data, label)
    if depth > 2:
        _fail(f'{label} 不支持嵌套序列')
    if not draft and data.get('schema') != 1:
        _fail(f'{label} 方案版本不受支持')
    if 'schema' in data and (type(data['schema']) is not int or data['schema'] != 1):
        _fail(f'{label} 方案版本不受支持')
    required = ('name', 'initial', 'safe', 'update_us', 'repeat', 'trigger', 'id', 'schema', 'segments')
    if not draft and any(key not in data for key in required):
        _fail(f'{label} 缺少方案字段')
    defaults = Profile().to_dict()
    defaults.update(sequence_mode='timeline', sequence_steps=[], scan_start=0, scan_step=1,
                    scan_points=1024, scan_end='hold', playback='hold', interpolation_error=1)
    known = {f.name for f in fields(Profile)}
    for key, value in data.items():
        if key not in known:
            continue  # Same forward-compatible behavior as Profile.from_dict.
        default = defaults.get(key)
        if key in ('segments', 'sequence_steps'):
            if not isinstance(value, list) or len(value) > 64:
                _fail(f'{label}.{key} 必须为最多 64 项的列表')
        elif type(default) is int:
            if type(value) is not int:
                _fail(f'{label}.{key} 必须是整数')
        elif type(default) is float:
            if type(value) not in (int, float):
                _fail(f'{label}.{key} 必须是数值')
        elif isinstance(default, str) and not isinstance(value, str):
            _fail(f'{label}.{key} 必须是文本')
    if 'id' in data and not _ID.fullmatch(data['id']):
        _fail(f'{label} 方案 ID 无效')
    for segment in data.get('segments', []):
        if not isinstance(segment, dict) or set(segment) != {'target', 'duration_ms'}:
            _fail(f'{label} 分段结构无效')
        if type(segment['target']) is not int or type(segment['duration_ms']) not in (int, float):
            _fail(f'{label} 分段数值类型无效')
    for step in data.get('sequence_steps', []):
        _object(step, label + ' 步骤')
        if 'waveform' in step:
            _profile(step['waveform'], label + ' 步骤波形', draft=draft, depth=depth + 1)
            if step['waveform'].get('mode') not in ('ramp', 'function'):
                _fail(f'{label} 步骤必须包含 Ramp 或函数快照')
        elif not draft:
            _fail(f'{label} 步骤缺少波形快照')
        for key in ('repeat', 'input'):
            if key in step and type(step[key]) is not int:
                _fail(f'{label} 步骤 {key} 类型无效')
        for key in ('start_ms', 'delay_ms'):
            if key in step and type(step[key]) not in (int, float):
                _fail(f'{label} 步骤时间类型无效')
    if draft:
        return  # Unsaved invalid expressions and unfinished values stay intact.
    p = defaults | data
    if not p['name'].strip() or len(p['name']) > 100:
        _fail(f'{label} 名称需为 1～100 个字符')
    if p['mode'] not in ('ramp', 'function', 'segments', 'sequence', 'scan'):
        _fail(f'{label} 波形类型无效')
    if p['dac_bits'] not in (10, 12, 14):
        _fail(f'{label} DAC 位数无效')
    if p['playback'] not in ('hold', 'linear'):
        _fail(f'{label} 播放编码无效')
    _integer(p['interpolation_error'], label + '.interpolation_error', 0, 16383)
    for key in ('initial', 'safe', 'amplitude'):
        _integer(p[key], label + '.' + key, 0, (1 << p['dac_bits']) - 1)
    for key in ('channel', 'trigger_input'):
        _integer(p[key], label + '.' + key, 0, 7)
    _integer(p['repeat'], label + '.repeat', 0, 65535)
    if p['trigger'] not in ('software', 'external') or p['trigger_edge'] not in ('rising', 'falling'):
        _fail(f'{label} 触发设置无效')
    if p['timing_mode'] not in ('fixed', 'gate') or p['function_layout'] not in ('single', 'staged'):
        _fail(f'{label} 时间/函数结构无效')
    if p['mode'] == 'function':
        from .functions import expression
        # AST whitelist only. In particular, do not call Profile.from_dict:
        # its staged endpoint validation evaluates imported expressions.
        expressions = [p['function']] if p['function_layout'] == 'single' else [p['rise_function'], p['fall_function']]
        for text in expressions:
            expression(text)
    if p['mode'] == 'sequence' and (p['sequence_mode'] not in ('timeline', 'events') or not p['sequence_steps']):
        _fail(f'{label} 序列模式或步骤无效')


def _assignments(data, profile_ids, warnings, label):
    _object(data, label)
    for ch, assignment in data.items():
        if ch not in _CHANNELS:
            _fail(f'{label} DAC 编号无效')
        _object(assignment, label + '.' + ch)
        profile = assignment.get('profile')
        if profile is not None and (not isinstance(profile, str) or not _ID.fullmatch(profile)):
            _fail(f'{label} 方案引用无效')
        if profile and profile not in profile_ids:
            warnings.add(f'DAC {int(ch)+1} 的所选方案已不在方案库中，导入后需重新选择')
        if 'enabled' in assignment and type(assignment['enabled']) is not bool:
            _fail(f'{label} 选择状态无效')
        override = _object(assignment.get('trigger_override', {}), label + ' 触发覆盖')
        if 'trigger' in override and override['trigger'] not in ('external', 'software'):
            _fail(f'{label} 触发源无效')
        if 'trigger_edge' in override and override['trigger_edge'] not in ('rising', 'falling'):
            _fail(f'{label} 触发边沿无效')
        if 'trigger_input' in override:
            _integer(override['trigger_input'], label + ' 触发输入', 0, 7)


def _designer(data, label):
    _object(data, label)
    _profile(data.get('profile'), label + '.profile', draft=True)
    for key in ('values', 'texts', 'combos', 'units'):
        if key in data:
            _object(data[key], label + '.' + key)
    if 'saved_fingerprint' in data and not isinstance(data['saved_fingerprint'], str):
        _fail(f'{label} 保存标识无效')
    if any(not isinstance(v, str) for v in data.get('texts', {}).values()):
        _fail(f'{label} 编辑文本无效')
    if any(type(v) not in (int, float) for v in data.get('values', {}).values()):
        _fail(f'{label} 编辑数值无效')
    if any(not isinstance(v, str) or v not in _TIME_UNITS for v in data.get('units', {}).values()):
        _fail(f'{label} 时间单位无效')
    table = data.get('table', [])
    if not isinstance(table, list) or len(table) > 64 or any(
            not isinstance(row, list) or len(row) != 4 or any(not isinstance(v, str) for v in row) for row in table):
        _fail(f'{label} 编辑表格无效')


def _boot(data, label):
    for key, value in _object(data, label).items():
        if key not in _CHANNELS:
            _fail(f'{label} DAC 编号无效')
        _profile(value, label + '.' + key)


def _validate(files):
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES:
        _fail('配置文件数量无效或归档为空')
    if sum(len(v) for v in files.values() if isinstance(v, bytes)) > MAX_TOTAL_BYTES:
        _fail('配置数据超过 64 MiB')
    data = {}; warnings = set(); ids = set(); modes = []
    for name, raw in files.items():
        _name(name)
        if not isinstance(raw, bytes) or len(raw) > MAX_FILE_BYTES:
            _fail(f'{name} 文件过大或内容无效')
        if name == '.sample_profiles_added':
            if len(raw) > 1024:
                _fail('示例标记文件过大')
            continue
        obj = data[name] = _json(raw, name)
        if name.startswith('profiles/'):
            _profile(obj, name)
            if name != f'profiles/{obj["id"]}.json':
                _fail(f'{name} 文件名与方案 ID 不一致')
            ids.add(obj['id']); modes.append(obj.get('mode', 'segments'))
    for name, obj in data.items():
        if name.startswith('profiles/'):
            continue
        if name == 'board.json':
            from .board import Board, validate
            try:
                defaults = Board().to_dict()
                for key, value in obj.items():
                    default = defaults.get(key)
                    if type(default) is bool and type(value) is not bool:
                        _fail(f'板卡 {key} 必须是布尔值')
                    elif type(default) is int and type(value) is not int:
                        _fail(f'板卡 {key} 必须是整数')
                    elif type(default) is float and type(value) not in (int, float):
                        _fail(f'板卡 {key} 必须是数值')
                    elif isinstance(default, str) and not isinstance(value, str):
                        _fail(f'板卡 {key} 必须是文本')
                    elif isinstance(default, dict) and not isinstance(value, dict):
                        _fail(f'板卡 {key} 必须是对象')
                board = Board.from_dict(obj)
                errors = validate(board)
                if errors:
                    _fail('板卡配置无效：' + '；'.join(errors))
                _boot(board.boot_profiles, '板卡上电方案')
            except (TypeError, KeyError, AttributeError) as exc:
                raise ValueError(f'全局工作区：板卡配置结构无效：{exc}') from exc
        elif name in ('designer_draft.json', 'dac_editors.json'):
            drafts = _object(obj.get('drafts'), name + '.drafts')
            _integer(obj.get('selected', 0), name + '.selected', 0, 7)
            for ch, draft in drafts.items():
                if ch not in _CHANNELS:
                    _fail(f'{name} DAC 编号无效')
                _designer(draft, name + '.' + ch)
        elif name == 'sequence_draft.json':
            _profile(obj, name, draft=True)
        elif name == 'scan_draft.json':
            _profile(obj.get('profile'), name + '.profile', draft=True)
            if 'saved' in obj and not isinstance(obj['saved'], str):
                _fail('扫描草稿保存标识无效')
            if 'fresh' in obj and type(obj['fresh']) is not bool:
                _fail('扫描草稿状态无效')
        elif name == 'parallel.json':
            _assignments(obj, ids, warnings, name)
        elif name == 'flash_selection.json':
            if any(k not in _CHANNELS or type(v) is not bool for k, v in obj.items()):
                _fail('Flash 通道选择格式无效')
        elif name == 'desktop_session.json':
            if type(obj.get('schema')) is not int or obj['schema'] != 1:
                _fail('界面设置快照版本不受支持')
            for key in ('board', 'view', 'debug', 'sequence', 'scan', 'assignments'):
                if key in obj:
                    _object(obj[key], '界面设置.' + key)
            if obj.get('designer') is not None:
                _designer(obj['designer'], '界面设置.designer')
            for key in ('sequence', 'scan'):
                if key in obj:
                    _profile(obj[key].get('profile'), '界面设置.' + key, draft=True)
                    for field in ('saved', 'loaded'):
                        if field in obj[key] and not isinstance(obj[key][field], str):
                            _fail(f'界面设置.{key}.{field} 必须是文本')
            if 'sequence' in obj:
                sequence = obj['sequence']
                for key in ('finite_repeat', 'row'):
                    if key in sequence and type(sequence[key]) is not int:
                        _fail(f'序列草稿 {key} 必须是整数')
                if 'time_unit' in sequence and (not isinstance(sequence['time_unit'], str) or sequence['time_unit'] not in _TIME_UNITS):
                    _fail('序列草稿时间单位无效')
            if 'assignments' in obj:
                _assignments(obj['assignments'], ids, warnings, '界面 DAC 分配')
            if 'board' in obj:
                b = obj['board']
                if type(b.get('schema')) is not int or b['schema'] != 1:
                    _fail('板卡草稿版本不受支持')
                for key in ('fields', 'pins', 'io_overrides', 'dac_timing', 'clock_standards', 'flash'):
                    if key in b:
                        _object(b[key], '板卡草稿.' + key)
                if 'boot_profiles' in b:
                    _boot(b['boot_profiles'], '板卡草稿上电方案')
            view = obj.get('view', {})
            for key in ('page', 'designer_tab', 'board_tab', 'active_dac'):
                if key in view and type(view[key]) is not int:
                    _fail('界面页码类型无效')
            for key in ('geometry', 'search'):
                if key in view and not isinstance(view[key], str):
                    _fail('界面文本设置无效')
            if view.get('geometry'):
                try:
                    base64.b64decode(view['geometry'].encode('ascii'), validate=True)
                except (ValueError, UnicodeError) as exc:
                    raise ValueError('全局工作区：窗口几何设置编码无效') from exc
            for key in ('maximized', 'connection_expanded', 'library_expanded', 'channel_settings', 'channel_versions', 'resource_details'):
                if key in view and type(view[key]) is not bool:
                    _fail(f'界面设置.{key} 必须是布尔值')
            debug = obj.get('debug', {})
            for key in ('channel', 'code', 'safe'):
                if key in debug:
                    _integer(debug[key], 'Debug ' + key, 0, 7 if key == 'channel' else 16383)
            if 'port' in view and view['port'] is not None and not isinstance(view['port'], str):
                _fail('串口选择无效')
            if 'baudrate' in view:
                from .uart_settings import BAUD_RATES
                if type(view['baudrate']) is not int or view['baudrate'] not in BAUD_RATES:
                    _fail('串口波特率无效')
    if not ids and not any(key in data for key in ('board.json', 'desktop_session.json')):
        _fail('归档不含板卡、界面设置或方案库')
    return dict(board_name=data.get('board.json', {}).get('name', ''), profiles=len(ids), files=len(files),
                waveform_count=sum(m not in ('sequence', 'scan') for m in modes),
                sequence_count=modes.count('sequence'), scan_count=modes.count('scan'), warnings=sorted(warnings))


def _safe_root(root):
    root = Path(root).absolute()
    if root.is_symlink() or (hasattr(root, 'is_junction') and root.is_junction()):
        _fail('数据目录不能是链接或联接点')
    return root.resolve()


def _local(root, name):
    path = root / name
    cursor = path
    while cursor != root:
        if cursor.is_symlink() or (hasattr(cursor, 'is_junction') and cursor.is_junction()):
            _fail(f'配置路径不能是链接或联接点：{name}')
        cursor = cursor.parent
    if not path.resolve().is_relative_to(root):
        _fail('配置路径超出当前数据目录')
    return path


def _collect(root):
    result = {}
    names = set(MANAGED_FILES)
    profiles = _local(root, 'profiles')
    if profiles.exists():
        if not profiles.is_dir():
            _fail('profiles 必须是目录')
        for path in profiles.glob('*.json'):
            names.add('profiles/' + path.name)
    for name in sorted(names):
        path = _local(root, name)
        if path.exists():
            _name(name)
            if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                _fail(f'{name} 不是可读取的配置文件')
            result[name] = path.read_bytes()
            if len(result) > MAX_FILES or sum(map(len, result.values())) > MAX_TOTAL_BYTES:
                _fail('配置文件数量或容量超限')
    return result


def _write_archive(files, path, created_at=None):
    summary = _validate(files)
    created_at = created_at or datetime.now(timezone.utc).isoformat(timespec='seconds')
    summary.update(created_at=created_at, created_utc=created_at, profile_count=summary['profiles'], file_count=summary['files'])
    manifest = dict(format=FORMAT, schema=SCHEMA, created_at=created_at, summary=summary,
                    files=[dict(path=name, size=len(raw), sha256=hashlib.sha256(raw).hexdigest()) for name, raw in sorted(files.items())])
    path = Path(path).absolute()
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        _fail('保存目标不能是链接')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.workspace-save-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w+b') as stream:
            with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, allow_nan=False, indent=2).encode('utf-8'))
                for name, raw in sorted(files.items()):
                    archive.writestr(name, raw)
            stream.flush(); os.fsync(stream.fileno())
        read_archive(temp)  # Verify compressed bytes and CRC before replacing a previous save.
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return summary


def save_archive(root, path):
    """Export the on-disk settings; callers first save their current UI drafts."""
    root = _safe_root(root)
    target = Path(path).absolute().resolve()
    if target.is_relative_to(root):
        relative = target.relative_to(root).as_posix()
        if relative in MANAGED_FILES or relative.startswith('profiles/'):
            _fail('不能用归档覆盖工作区配置或方案文件')
    return _write_archive(_collect(root), path)


def read_archive(path):
    """Validate a ZIP in memory. No extraction and no configuration path access."""
    path = Path(path)
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        _fail('归档文件超过 72 MiB')
    try:
        with zipfile.ZipFile(path, 'r') as archive:
            infos = archive.infolist()
            if not 2 <= len(infos) <= MAX_FILES + 1:
                _fail('归档文件数量无效')
            names = [info.filename for info in infos]
            if len(set(n.casefold() for n in names)) != len(names) or names.count('manifest.json') != 1:
                _fail('归档包含重复文件或缺少清单')
            total = 0
            for info in infos:
                if info.filename != 'manifest.json':
                    _name(info.filename)
                mode = (info.external_attr >> 16) & 0o170000
                if info.is_dir() or mode not in (0, stat.S_IFREG) or info.flag_bits & 1:
                    _fail('归档不支持目录、链接或加密项')
                limit = MAX_MANIFEST_BYTES if info.filename == 'manifest.json' else MAX_FILE_BYTES
                if info.file_size > limit:
                    _fail(f'{info.filename} 超过文件容量限制')
                total += info.file_size
            if total > MAX_TOTAL_BYTES + MAX_MANIFEST_BYTES:
                _fail('解压后配置数据超过容量限制')
            manifest = _json(archive.read('manifest.json'), 'manifest.json')
            if manifest.get('format') != FORMAT or type(manifest.get('schema')) is not int or manifest['schema'] != SCHEMA:
                _fail('不是支持的 Ramp Studio 全局工作区版本')
            created = manifest.get('created_at')
            try:
                stamp = datetime.fromisoformat(created)
                if stamp.utcoffset() != timezone.utc.utcoffset(stamp):
                    _fail('清单时间必须使用 UTC')
            except (TypeError, ValueError) as exc:
                raise ValueError('全局工作区：清单创建时间无效') from exc
            entries = manifest.get('files')
            if not isinstance(entries, list) or len(entries) != len(infos) - 1:
                _fail('文件清单数量不一致')
            files = {}
            for entry in entries:
                _object(entry, '文件清单项')
                name = _name(entry.get('path'))
                if name in files or name not in names:
                    _fail('文件清单缺失或重复')
                if type(entry.get('size')) is not int or not isinstance(entry.get('sha256'), str):
                    _fail('文件清单摘要无效')
                raw = archive.read(name)  # zipfile checks each member CRC here.
                if len(raw) != entry['size'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
                    _fail(f'{name} 大小或 SHA-256 校验失败')
                files[name] = raw
            summary = _validate(files)
            summary.update(created_at=created, created_utc=created, profile_count=summary['profiles'], file_count=summary['files'])
            return WorkspaceSnapshot(files, summary)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error) as exc:
        raise ValueError(f'全局工作区：归档损坏或压缩格式不受支持：{exc}') from exc


def restore_archive(root, snapshot):
    """Replace managed files, with a verified recovery archive and byte rollback."""
    if not isinstance(snapshot, WorkspaceSnapshot):
        _fail('请先读取并校验全局归档')
    files = dict(snapshot.files)
    _validate(files)  # Recheck caller-modified snapshots before touching disk.
    root = _safe_root(root)
    root.mkdir(parents=True, exist_ok=True)
    before = _collect(root)
    names = sorted(set(before) | set(files))
    for name in names:
        _local(root, name)
    folder = _local(root, 'archive/workspaces')
    folder.mkdir(parents=True, exist_ok=True)
    backup = folder / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:8] + '-before-import' + SUFFIX)
    _write_archive(before, backup)
    changed = []
    try:
        with tempfile.TemporaryDirectory(prefix='.workspace-import-', dir=root) as temporary:
            stage = Path(temporary)
            for index, name in enumerate(names):
                target = _local(root, name)
                if name in files:
                    staged = stage / str(index)
                    with staged.open('wb') as stream:
                        stream.write(files[name]); stream.flush(); os.fsync(stream.fileno())
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(staged, target)
                elif target.exists():
                    target.unlink()
                changed.append(name)
    except BaseException as original:
        rollback_errors = []
        for name in reversed(changed):
            try:
                target = _local(root, name)
                if name in before:
                    fd, temporary = tempfile.mkstemp(prefix='.workspace-rollback-', dir=target.parent)
                    try:
                        with os.fdopen(fd, 'wb') as stream:
                            stream.write(before[name]); stream.flush(); os.fsync(stream.fileno())
                        os.replace(temporary, target)
                    finally:
                        if os.path.exists(temporary):
                            os.unlink(temporary)
                elif target.exists():
                    target.unlink()
            except OSError as exc:
                rollback_errors.append(f'{name}: {exc}')
        if rollback_errors:
            raise OSError(f'导入失败且回滚未完成，请使用恢复包 {backup}；' + '；'.join(rollback_errors)) from original
        raise OSError(f'导入失败，原配置已恢复。恢复包：{backup}；{original}') from original
    return backup
