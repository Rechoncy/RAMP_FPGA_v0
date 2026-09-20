"""Parallel reply-cache refactoring must preserve every external output clock."""
from pathlib import Path
import hashlib
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('bits', [10, 12, 14])
def test_parallel_all_outputs_match_frozen_reply_cache(tmp_path, bits):
    if not shutil.which('iverilog') or not shutil.which('vvp'):
        pytest.skip('Icarus Verilog required')
    root = Path(__file__).resolve().parents[1]
    frozen = root / 'tests/fixtures/parallel_controller_before_cache_reuse.v'
    # Freeze only the parent controller; both copies use identical child engines.
    # Normalize line endings so a checkout does not invalidate the source guard.
    source = frozen.read_text().replace('\r\n', '\n')
    assert hashlib.sha256(source.encode()).hexdigest() == (
        'a6dd889de7e80121970b78d2b817892c5587ddaa9de784ff4643585e791ce18c'
    )
    # The frozen parent's logic is unchanged. Adapt only declared INFO
    # capability additions for batch uploads.
    source = source.replace('reply[207:200]<=1;', 'reply[207:200]<=35;')
    source = source.replace('reply[223:216]<=1;', 'reply[223:216]<=1;reply[231:224]<=1;reply[239:232]<=3;')
    frozen = tmp_path / 'adapted_frozen.v'
    frozen.write_text(source)
    sources = list((root / 'rtl/common').glob('*.v'))
    output = tmp_path / 'parallel_compare.vvp'
    compiled = subprocess.run([
        'iverilog', '-g2012', '-s', 'tb_parallel_cycle_compatibility',
        f'-Ptb_parallel_cycle_compatibility.BITS={bits}', '-o', str(output),
        *map(str, sources), str(frozen),
        str(root / 'tests/rtl/tb_parallel_cycle_compatibility.v'),
    ], capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stderr
    result = subprocess.run(['vvp', str(output)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS frozen parallel output comparison' in result.stdout
