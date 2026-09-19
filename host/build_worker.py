"""QProcess child: runs Vivado without freezing the desktop UI."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import locale
from .board import Board, generate_xdc, clock_ports
from .runtime import build_script, prepare_external_runtime, resource_root
from .utilization import source_fingerprint, write_build_manifest, generated_interface_hashes


def main():
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8",errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--mode", choices=["generate", "synth", "check", "implement"], default="generate")
    args = parser.parse_args()
    b = Board.from_dict(json.loads(Path(args.board).read_text(encoding="utf-8")))
    output = Path(args.output).resolve()
    if output.exists():
        raise ValueError(f"Output directory already exists: {output}")
    context = {"source_root": resource_root()}
    status, error = "failed", ""
    try:
        code = run_build(args, b, context)
        status = "success" if code == 0 else "failed"
        if code:
            error = f"Vivado exited with code {code}; see build log"
        return code
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        try:
            manifest = write_build_manifest(output, b, args.mode, status, context["source_root"],
                error, context.get("source_fingerprint"), context.get("xdc"), context.get("boot_dir"),
                context.get('interfaces_dir'),context.get('initial_interface_sources'))
            print(f"Resource report index: {manifest['manifest_path']} ({status})", flush=True)
        except Exception as exc:
            # Never replace the original Vivado exit/failure with a UI index error.
            print(f"RESOURCE REPORT WARNING: {exc}", file=sys.stderr, flush=True)


def run_build(args, b, context):
    xdc = Path(args.board).with_suffix(".xdc")
    context["xdc"] = xdc
    xdc.write_text(generate_xdc(b, complete=args.mode == "implement"), encoding="utf-8")
    script = build_script(args.output)
    context["source_root"] = script.parents[2]
    context["source_fingerprint"] = source_fingerprint(context["source_root"])
    # Windows batch files are interpreted by cmd; reject metacharacters in paths.
    paths = [b.vivado, str(script), str(xdc), args.output]
    if any(any(c in s for c in '%&|<>^!"\r\n') for s in paths):
        raise ValueError("构建路径不能包含 cmd 特殊字符，如 %、& 或 !")
    if not Path(b.vivado).is_file():
        raise FileNotFoundError(f"找不到 Vivado：{b.vivado}")
    from .flash import FlashSettings
    flash=FlashSettings.from_dict(b.flash);flash.validate()
    boot_dir=''
    if b.boot_profiles:
        from .boot import generate_boot
        boot_dir=str(Path(args.output).resolve().with_name(Path(args.output).name+'_boot'))
        context["boot_dir"] = boot_dir
        manifest=generate_boot(b,boot_dir)
        print(f'Boot snapshot: {len(manifest)} DAC(s); sources and manifest: {boot_dir}',flush=True)
    command = [b.vivado, "-mode", "batch", "-nojournal", "-nolog", "-source", str(script),
               "-tclargs", b.part, str(b.ref_mhz), str(xdc), args.output, args.mode, str(b.dac_count), str(b.trigger_count), str(b.dac_bits),flash.interface,str(flash.config_rate),str(int(flash.falling_edge))]
    command.extend([boot_dir or '-',b.clock_input])
    reference_pins=[b.pins.get(p,'').strip().upper() or '-' for p in clock_ports(b.clock_input)]
    command.extend(reference_pins+(['-'] if b.clock_input=='single_ended' else []))
    command.append(str(b.uart_baud))
    from .extended_build import stage_platform
    interfaces=stage_platform(b,args.output)
    if interfaces:
        context['interfaces_dir']=interfaces
        context['initial_interface_sources']=generated_interface_hashes(interfaces)
        if any(c in str(interfaces) for c in '%&|<>^!"\r\n'):
            raise ValueError('扩展接口构建目录包含不支持的路径字符')
        command.append(str(interfaces))
        print(f'Optional interface sources: {interfaces}; USB FIFO + UART, waveform storage remains BRAM.',flush=True)
    if not interfaces: command.append('-')
    command.append(str(int(b.core_mhz)))
    print(f'Reference clock: {b.ref_mhz:g} MHz, {b.clock_input}; core/DAC: {b.core_mhz:g} MHz',flush=True)
    print(f'UART power-on default: {b.uart_baud} baud; automatic connection-time selection supports 9600..1000000 baud.',flush=True)
    print("Starting Vivado; IP generation is not proof of full-system timing closure.", flush=True)
    prepare_external_runtime()
    child = subprocess.Popen(command, cwd=script.parents[2], stdout=subprocess.PIPE,
        stdin=subprocess.DEVNULL, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for raw in child.stdout:
        try: line=raw.decode("utf-8")
        except UnicodeDecodeError: line=raw.decode(locale.getpreferredencoding(False),errors="replace")
        print(line,end="",flush=True)
    return child.wait()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"BUILD ERROR: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
