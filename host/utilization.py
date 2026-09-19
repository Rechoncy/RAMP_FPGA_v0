"""Read Vivado utilization reports without mistaking them for live telemetry.

Build manifests retain configuration and source identity. Report paths in those
manifests are relative to their build directory so copied libraries keep working.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re


MAX_REPORT_BYTES = 4 * 1024 * 1024
MANIFEST_NAME = "resource_report.json"
_METRICS = {
    "slice luts": ("lut", "LUT"), "clb luts": ("lut", "LUT"),
    "slice registers": ("ff", "FF"), "clb registers": ("ff", "FF"),
    "block ram tile": ("bram", "36 Kb"),
    "dsps": ("dsp", "DSP"), "dsp48e1": ("dsp", "DSP"),
    "dsp48e2": ("dsp", "DSP"), "dsp48e": ("dsp", "DSP"),
}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _number(value):
    text = value.strip().replace(",", "").rstrip("%").strip()
    if text.upper() in ("", "N/A", "NA", "--", "-", "NAN", "INF"):
        return None
    try:
        number = float(text.lstrip("<>~"))
    except ValueError:
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return int(number) if number.is_integer() else number


def read_report(path, stage=None):
    """Return plain dictionaries; unavailable resource fields remain None.

    Only top-level resource rows with the complete utilization table header are
    considered. In particular RAMB18 and RAMB36 children are never added again.
    """
    path = Path(path).expanduser().resolve()
    if path.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError("资源报告超过 4 MiB，请导入标准 report_utilization 报告")
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig", errors="replace")
    report = {"schema": 1, "path": str(path), "sha256": _sha(raw),
              "device": "", "date": "", "design": "", "design_state": "", "tool": "",
              "stage": "unknown", "metrics": dict.fromkeys(("lut", "ff", "bram", "dsp")),
              "warnings": []}
    headers = {"Tool Version": "tool", "Date": "date", "Device": "device",
               "Design": "design", "Design State": "design_state"}
    columns = None
    for line in text.splitlines():
        match = re.match(r"^\s*\|\s*([^|:]+?)\s*:\s*(.*?)\s*\|?\s*$", line)
        if match and match[1] in headers:
            report[headers[match[1]]] = match[2].strip()
            continue
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        lower = [cell.casefold() for cell in cells]
        if "used" in lower and "available" in lower and "util%" in lower:
            columns = {key: lower.index(key) for key in ("used", "available", "util%")}
            continue
        # A different table header terminates the preceding numeric schema.
        if cells and cells[0].casefold() in ("site type", "ref name", "primitive", "total"):
            columns = None
            continue
        label = re.sub(r"\s+", " ", cells[0].rstrip("* ")).casefold() if cells else ""
        if not columns or label not in _METRICS or len(cells) <= max(columns.values()):
            continue
        key, unit = _METRICS[label]
        if report["metrics"][key] is not None:
            continue
        used, available, percent = (cells[columns[c]] for c in ("used", "available", "util%"))
        report["metrics"][key] = {
            "label": cells[0], "unit": unit, "used": _number(used),
            "available": _number(available), "percent": _number(percent),
            "raw_percent": percent,
        }
    state = report["design_state"].casefold()
    inferred = "routed" if "rout" in state else "synth" if "synth" in state else "unknown"
    report["stage"] = stage if stage in ("routed", "synth", "unknown") else inferred
    if not any(report["metrics"].values()):
        raise ValueError("未找到 LUT、FF、BRAM 或 DSP 的资源总计，请导入 Vivado report_utilization 报告")
    for key, metric in report["metrics"].items():
        if metric is None or any(metric[field] is None for field in ("used", "available", "percent")):
            report["warnings"].append(f"{key.upper()} 的部分统计未提供")
    return report


def board_fingerprint(board):
    """Hardware/build settings only: names and local Vivado paths are cosmetic."""
    from .board import Board
    from .flash import FlashSettings
    raw = board.to_dict() if hasattr(board, "to_dict") else dict(board)
    config = Board.from_dict({"schema": 3, **raw}).to_dict()
    for key in ("name", "vivado", "schema"):
        config.pop(key, None)
    if not config.get('dac_model'):config.pop('dac_model',None)
    for key in ("ref_mhz", "core_mhz", "config_voltage", "dac_delay_min", "dac_delay_max"):
        if config[key] is not None:
            config[key] = float(config[key])
    flash = FlashSettings.from_dict(config["flash"])
    config["flash"] = {"interface": flash.interface, "config_rate": flash.config_rate,
                       "falling_edge": flash.falling_edge}
    config["pins"] = {key: value.strip().upper() for key, value in config["pins"].items()}

    def canonical(value):
        if isinstance(value, float) and math.isfinite(value) and value.is_integer():
            return int(value)
        if isinstance(value, dict):
            return {key: canonical(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [canonical(item) for item in value]
        return value

    config = canonical(config)
    return _sha(json.dumps(config, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False).encode("utf-8"))


def source_fingerprint(root):
    """Hash the RTL and Tcl files used by a generated independent project."""
    root = Path(root).resolve()
    files = []
    for relative, pattern in (("rtl/common", "*.v"), ("rtl/platform/xilinx_7series", "*.v"),
                              ("scripts/vivado", "*.tcl"),("scripts/vivado", "*.xdc")):
        files.extend((root / relative).glob(pattern))
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError("构建源码不能通过链接指向源目录之外")
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(bytes.fromhex(_sha(path.read_bytes())))
    return digest.hexdigest()


def _inside(root, relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("构建报告路径必须位于其构建目录内")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("构建报告链接指向了构建目录之外")
    return resolved


def generated_interface_hashes(directory):
    """Record generated top source bytes, without following external links."""
    if not directory:return {}
    folder=Path(directory).resolve()
    if not folder.is_dir():return {}
    result={}
    for path in sorted(folder.glob('*.v')):
        if not path.resolve().is_relative_to(folder):
            raise ValueError('扩展接口源码不能通过链接指向构建目录之外')
        result[path.name]=_sha(path.read_bytes())
    return result


def write_build_manifest(output, board, mode, status, source_root, error=None,
                         initial_source_fingerprint=None, xdc=None, boot_dir=None,
                         interfaces_dir=None, initial_interface_sources=None):
    """Record even failed builds; a synth report does not imply routed success."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    reports, warnings = [], []
    if mode != "generate":
        for relative, stage in (("utilization_synth.rpt", "synth"),
                                ("core_route_check/utilization.rpt", "routed"),
                                ("utilization_routed.rpt", "routed")):
            path = _inside(output, relative)
            if path.is_file():
                try:
                    report = read_report(path, stage)
                    report["path"] = relative
                    reports.append(report)
                except (OSError, ValueError) as exc:
                    warnings.append(f"{relative}: {exc}")
    final_source = source_fingerprint(source_root)
    initial_source = initial_source_fingerprint or final_source
    manifest = {
        "schema": 1, "updated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode, "status": status, "error": str(error or ""),
        "board_part": board.part, "board_fingerprint": board_fingerprint(board),
        "board_context": {"part": board.part, "dac_count": board.dac_count,
                          "dac_bits": board.dac_bits, "trigger_count": board.trigger_count,
                          "ref_mhz": board.ref_mhz, "clock_input": board.clock_input,
                          "core_mhz": board.core_mhz, "dac_model": board.dac_model,
                          "uart_baud": board.uart_baud,
                          "boot_channels": sorted(int(key) + 1 for key in board.boot_profiles)},
        "source_fingerprint": initial_source, "final_source_fingerprint": final_source,
        "sources_changed": initial_source != final_source,
        "reports": reports, "warnings": warnings,
    }
    if xdc and Path(xdc).is_file():
        manifest["xdc_sha256"] = _sha(Path(xdc).read_bytes())
    if boot_dir and Path(boot_dir).is_dir():
        boot_path = Path(boot_dir).resolve()
        manifest["boot_sources"] = {
            path.name: _sha(path.read_bytes()) for path in sorted(boot_path.iterdir())
            if path.is_file() and path.resolve().is_relative_to(boot_path)
        }
    if interfaces_dir:
        final_interfaces=generated_interface_hashes(interfaces_dir)
        initial_interfaces=final_interfaces if initial_interface_sources is None else initial_interface_sources
        manifest['interface_sources']=initial_interfaces
        manifest['final_interface_sources']=final_interfaces
        manifest['interfaces_changed']=initial_interfaces!=final_interfaces
        manifest['sources_changed']|=manifest['interfaces_changed']
    destination = output / MANIFEST_NAME
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return read_build_manifest(destination)


def read_build_manifest(path):
    path = Path(path).resolve()
    if path.is_dir():
        path = path / MANIFEST_NAME
    if path.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError("资源构建索引过大")
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        raise ValueError("不支持的资源构建索引")
    if manifest.get("mode") not in ("generate", "synth", "check", "implement"):
        raise ValueError("资源构建索引的构建模式无效")
    if manifest.get("status") not in ("success", "failed"):
        raise ValueError("资源构建索引的构建状态无效")
    root, reports = path.parent, []
    warnings = list(manifest.get("warnings", []))
    entries = manifest.get("reports", [])
    if not isinstance(entries, list):
        raise ValueError("资源报告索引格式无效")
    for saved in entries:
        if not isinstance(saved, dict) or not isinstance(saved.get("path"), str):
            raise ValueError("资源报告来源无效")
        report_path = _inside(root, saved["path"])
        try:
            report = read_report(report_path, saved.get("stage"))
            if report["sha256"] != saved.get("sha256"):
                warnings.append(f"{saved['path']} 内容已变化，未采用其资源统计")
                continue
            reports.append(report)
        except (OSError, ValueError) as exc:
            warnings.append(f"{saved['path']}: {exc}")
    # Generate-only has no synthesis even if an unrelated report was copied in.
    if manifest["mode"] == "generate":
        reports = []
    reports.sort(key=lambda r: r["stage"] == "routed")
    manifest.update(manifest_path=str(path), build_output=str(root), reports=reports,
                    report=reports[-1] if reports else None, warnings=warnings)
    return manifest


def find_latest_build(builds_root):
    """Inspect only direct build children, never follow links outside the root."""
    root = Path(builds_root).expanduser().resolve()
    if not root.is_dir():
        return None
    candidates = []
    for directory in root.iterdir():
        resolved = directory.resolve()
        if not directory.is_dir() or not resolved.is_relative_to(root):
            continue
        path = resolved / MANIFEST_NAME
        if not path.is_file() or not path.resolve().is_relative_to(resolved):
            continue
        try:
            manifest = read_build_manifest(path)
            date = datetime.fromisoformat(manifest["updated_at"])
            candidates.append((date.timestamp(), manifest))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return max(candidates, key=lambda item: item[0])[1] if candidates else None
