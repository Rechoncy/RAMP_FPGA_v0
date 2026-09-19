import copy
import json
from pathlib import Path
import shutil

import pytest

from host.board import Board
from host.utilization import (board_fingerprint, find_latest_build, read_build_manifest,
                              read_report, source_fingerprint, write_build_manifest, generated_interface_hashes)


REAL_REPORT = Path(__file__).resolve().parents[1] / "release/FPGA-XC7A35T-TriggerScan/utilization.rpt"


def test_generated_interface_sources_are_bound_to_build_report(tmp_path):
    generated=tmp_path/'generated';generated.mkdir()
    top=generated/'ramp_extended_top.v'
    top.write_text('module ramp_extended_top; endmodule\n',encoding='ascii')
    initial=generated_interface_hashes(generated)
    first=write_build_manifest(tmp_path/'unchanged',Board(),'generate','success',tmp_path,
                               interfaces_dir=generated,initial_interface_sources=initial)
    assert first['interface_sources']==first['final_interface_sources']==initial
    assert not first['sources_changed'] and not first['interfaces_changed']
    top.write_text('module different_top; endmodule\n',encoding='ascii')
    changed=write_build_manifest(tmp_path/'changed',Board(),'generate','success',tmp_path,
                                 interfaces_dir=generated,initial_interface_sources=initial)
    assert changed['interface_sources']==initial
    assert changed['final_interface_sources']!=initial
    assert changed['sources_changed'] and changed['interfaces_changed']


@pytest.fixture
def report_path(tmp_path):
    destination = tmp_path / "utilization.rpt"
    shutil.copyfile(REAL_REPORT, destination)
    return destination


def table(rows, title="Routed"):
    return "\n".join([
        "| Tool Version : Vivado v.2023.1 (win64)", "| Date : Mon Sep 14 14:25:00 2026",
        "| Device : xc7a35tfgg484-2", "| Design : ramp_top", f"| Design State : {title}",
        "| Site Type | Used | Fixed | Prohibited | Available | Util% |", *rows,
    ])


def test_real_routed_report_uses_only_parent_totals(report_path):
    result = read_report(report_path)
    assert result["device"] == "xc7a35tfgg484-2"
    assert result["stage"] == "routed" and result["design_state"] == "Routed"
    assert result["tool"].startswith("Vivado v.2023.1")
    assert result["date"] == "Sun Sep 13 19:00:18 2026"
    assert {key: (metric["used"], metric["available"], metric["percent"])
            for key, metric in result["metrics"].items()} == {
        "lut": (7803, 20800, 37.51), "ff": (10813, 41600, 25.99),
        "bram": (32, 50, 64), "dsp": (4, 90, 4.44),
    }
    assert result["warnings"] == []
    assert len(result["sha256"]) == 64 and Path(result["path"]).is_absolute()


def test_ultrascale_names_fractional_bram_and_over_capacity(tmp_path):
    path = tmp_path / "new.rpt"
    path.write_text(table([
        "| CLB LUTs* | 25,000 | 0 | 0 | 20,800 | 120.19 |",
        "| CLB Registers | 0 | 0 | 0 | 41,600 | 0.00 |",
        "| Block RAM Tile | 32.5 | 0 | 0 | 50 | 65.00 |",
        "| RAMB36/FIFO* | 32 | 0 | 0 | 50 | 64.00 |",
        "| RAMB18 | 1 | 0 | 0 | 100 | 1.00 |",
        "| DSP48E2 | 3 | 0 | 0 | 90 | 3.33 |",
    ], "Synthesized"), encoding="utf-8")
    result = read_report(path)
    assert result["stage"] == "synth"
    assert result["metrics"]["lut"]["used"] == 25000
    assert result["metrics"]["lut"]["percent"] == 120.19
    assert result["metrics"]["ff"]["used"] == 0
    assert result["metrics"]["bram"]["used"] == 32.5
    assert result["metrics"]["dsp"]["used"] == 3


def test_missing_and_na_values_stay_unknown(tmp_path):
    path = tmp_path / "partial.rpt"
    path.write_text(table([
        "| Slice LUTs | 1 | 0 | 0 | N/A | N/A |",
        "| Block RAM Tile | N/A | 0 | 0 | 50 | -- |",
        "| DSPs | 0 | 0 | 0 | 90 | 0.00 |",
    ]), encoding="utf-8")
    result = read_report(path)
    assert result["metrics"]["lut"]["available"] is None
    assert result["metrics"]["lut"]["percent"] is None
    assert result["metrics"]["ff"] is None
    assert result["metrics"]["bram"]["used"] is None
    assert result["metrics"]["dsp"]["used"] == 0
    assert len(result["warnings"]) == 3


def test_subtable_cannot_supply_missing_total_or_override_first_total(tmp_path):
    path = tmp_path / "nested.rpt"
    path.write_text(table([
        "| Slice LUTs | 5 | 0 | 0 | 100 | 5.00 |",
        "| Slice LUTs | 7 | 0 | 0 | 100 | 7.00 |",
        "| RAMB36E1 only | 2 | 0 | 0 | 50 | 4.00 |",
        "| Site Type | Used | Available |",
        "| Slice Registers | 1 | 0 | 0 | 100 | 1.00 |",
    ]), encoding="utf-8")
    result = read_report(path)
    assert result["metrics"]["lut"]["used"] == 5
    assert result["metrics"]["ff"] is None
    assert result["metrics"]["bram"] is None


def test_rejects_non_report_and_oversized_input(tmp_path):
    path = tmp_path / "other.rpt"
    path.write_text("not a report", encoding="utf-8")
    with pytest.raises(ValueError, match="report_utilization"):
        read_report(path)
    with path.open("wb") as stream:
        stream.truncate(4 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="4 MiB"):
        read_report(path)


def test_board_identity_excludes_local_paths_and_display_name():
    board = Board()
    same = copy.deepcopy(board)
    same.name = "Different display name"
    same.vivado = "D:/Vivado/bin/vivado.bat"
    assert board_fingerprint(same) == board_fingerprint(board)
    same.ref_mhz = 50
    same.config_voltage = "3.3"
    same.flash = {"bitfile": "D:/copied/firmware.bit", "target": "USB-OTHER"}
    assert board_fingerprint(same) == board_fingerprint(board)
    old_schema = {"schema": 1, "pins": {"sys_clk": "r4", "dipole_ramp_trigger": "F13"}}
    new_schema = {"schema": 3, "pins": {"sys_clk": "R4", "trigger_in[0]": "F13"}}
    assert board_fingerprint(old_schema) == board_fingerprint(new_schema)
    for key, value in (("part", "xc7a100tfgg484-2"), ("ref_mhz", 100),
                       ("clock_input", "differential"), ("dac_count", 4),
                       ("dac_bits", 14), ("trigger_count", 4)):
        changed = copy.deepcopy(board)
        setattr(changed, key, value)
        assert board_fingerprint(changed) != board_fingerprint(board), key
    changed = copy.deepcopy(board)
    changed.pins["sys_clk"] = "R5"
    assert board_fingerprint(changed) != board_fingerprint(board)


def test_source_identity_tracks_actual_rtl_and_tcl_not_host_ui(tmp_path):
    (tmp_path / "rtl/common").mkdir(parents=True)
    rtl = tmp_path / "rtl/common/core.v"
    rtl.write_text("module core; endmodule")
    first = source_fingerprint(tmp_path)
    (tmp_path / "host").mkdir()
    (tmp_path / "host/ui.py").write_text("print('UI')")
    assert source_fingerprint(tmp_path) == first
    rtl.write_text("module core; wire signal; endmodule")
    assert source_fingerprint(tmp_path) != first


def test_failed_implementation_preserves_synthesis_and_relative_sources(tmp_path, report_path):
    output = tmp_path / "builds/failed"
    output.mkdir(parents=True)
    shutil.copyfile(report_path, output / "utilization_synth.rpt")
    manifest = write_build_manifest(output, Board(), "implement", "failed", tmp_path,
                                    error="route timing not met")
    assert manifest["status"] == "failed" and manifest["mode"] == "implement"
    assert manifest["report"]["stage"] == "synth"
    assert manifest["error"] == "route timing not met"
    disk = json.loads((output / "resource_report.json").read_text(encoding="utf-8"))
    assert disk["reports"][0]["path"] == "utilization_synth.rpt"
    copied = tmp_path / "relocated/copy"
    shutil.copytree(output, copied)
    restored = read_build_manifest(copied)
    assert restored["report"]["path"] == str((copied / "utilization_synth.rpt").resolve())
    assert restored["board_fingerprint"] == board_fingerprint(Board())
    assert restored["board_context"] == {"part": Board().part, "dac_count": 2, "dac_bits": 10,
                                          "trigger_count": 2, "ref_mhz": 50.0, "core_mhz": 200.0, "dac_model": "",
                                          "clock_input": "single_ended", "uart_baud": 115200, "boot_channels": []}


def test_routed_precedence_generate_only_and_source_change(tmp_path, report_path):
    output = tmp_path / "implemented"
    output.mkdir()
    for filename in ("utilization_synth.rpt", "utilization_routed.rpt"):
        shutil.copyfile(report_path, output / filename)
    manifest = write_build_manifest(output, Board(), "implement", "success", tmp_path,
                                    initial_source_fingerprint="0" * 64)
    assert manifest["report"]["stage"] == "routed"
    assert manifest["sources_changed"] is True
    generated = write_build_manifest(output, Board(), "generate", "success", tmp_path)
    assert generated["report"] is None and generated["reports"] == []


def test_changed_report_is_not_trusted(tmp_path, report_path):
    output = tmp_path / "build"
    output.mkdir()
    target = output / "utilization_synth.rpt"
    shutil.copyfile(report_path, target)
    write_build_manifest(output, Board(), "synth", "success", tmp_path)
    with target.open("a", encoding="utf-8") as stream:
        stream.write("\nchanged")
    result = read_build_manifest(output)
    assert result["report"] is None
    assert any("内容已变化" in warning for warning in result["warnings"])


@pytest.mark.parametrize("unsafe", ["../outside.rpt", "C:/outside.rpt"])
def test_manifest_cannot_read_outside_its_build(tmp_path, report_path, unsafe):
    output = tmp_path / "build"
    manifest = write_build_manifest(output, Board(), "synth", "success", tmp_path)
    manifest["reports"] = [{"path": unsafe, "sha256": "0" * 64}]
    (output / "resource_report.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="构建目录"):
        read_build_manifest(output)


def test_latest_build_includes_failed_or_generate_only_not_older_success(tmp_path, report_path):
    builds = tmp_path / "builds"
    success = builds / "first"
    success.mkdir(parents=True)
    shutil.copyfile(report_path, success / "utilization_synth.rpt")
    write_build_manifest(success, Board(), "synth", "success", tmp_path)
    generated = builds / "second"
    write_build_manifest(generated, Board(), "generate", "success", tmp_path)
    assert find_latest_build(builds)["mode"] == "generate"
    failed = builds / "third"
    write_build_manifest(failed, Board(), "implement", "failed", tmp_path, error="Vivado unavailable")
    assert find_latest_build(builds)["status"] == "failed"
    # A deeper descendant is not an application-created build directory.
    write_build_manifest(builds / "unrelated/nested", Board(), "check", "success", tmp_path)
    assert find_latest_build(builds)["build_output"] == str(failed.resolve())


def test_build_worker_records_early_failure_without_touching_hardware(tmp_path, monkeypatch):
    from host import build_worker
    board = tmp_path / "board.json"
    board.write_text(json.dumps(Board(vivado=str(tmp_path / "missing-vivado.bat")).to_dict()), encoding="utf-8")
    output = tmp_path / "output"
    monkeypatch.setattr("sys.argv", ["build_worker", "--board", str(board), "--output", str(output), "--mode", "synth"])
    with pytest.raises(FileNotFoundError):
        build_worker.main()
    result = read_build_manifest(output)
    assert result["status"] == "failed" and result["report"] is None
    assert "Vivado" in result["error"]
    # Refusing an existing project must not replace its earlier provenance.
    original = (output / "resource_report.json").read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        build_worker.main()
    assert (output / "resource_report.json").read_bytes() == original
