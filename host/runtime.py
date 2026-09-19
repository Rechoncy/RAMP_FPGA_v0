"""Paths that stay valid in source, portable and read-only installations."""
import os
from pathlib import Path
import shutil
import sys
import tempfile


def frozen():
    return bool(getattr(sys, "frozen", False))


def resource_root():
    return Path(__file__).resolve().parents[1]


def application_dir():
    return Path(sys.executable).resolve().parent if frozen() else resource_root()


def data_dir():
    override = os.environ.get("RAMP_STUDIO_DATA_DIR")
    if override:
        path = Path(override).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path
    portable = application_dir() / "user_data"
    existed = portable.exists()
    try:
        portable.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=portable):
            pass
        return portable
    except OSError:
        if existed or not frozen():
            raise PermissionError(f"无法写入方案目录 {portable}。请将程序及 user_data 一起复制到可写文件夹。")
        try:
            portable.rmdir()  # Only remove the empty directory created by this failed probe.
        except OSError:
            pass
        local = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "RampStudio" / "user_data"
        local.mkdir(parents=True, exist_ok=True)
        return local


def build_script(output):
    root = resource_root()
    if frozen():
        # One-file resources disappear on exit. A generated XPR must retain its RTL.
        staged = Path(output).resolve().with_name(Path(output).name + "_sources")
        staged.mkdir(parents=True, exist_ok=False)
        for relative in ("rtl", "scripts/vivado"):
            shutil.copytree(root / relative, staged / relative)
        root = staged
    return root / "scripts/vivado/build_studio.tcl"


def prepare_external_runtime():
    """Called only by the disposable worker, never by the GUI process."""
    if not frozen():
        return
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    bundle = resource_root()
    os.environ["PATH"] = os.pathsep.join(
        item for item in os.environ.get("PATH", "").split(os.pathsep)
        if item and not Path(item).resolve().is_relative_to(bundle)
    )
    for key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "PYTHONHOME", "PYTHONPATH"):
        os.environ.pop(key, None)
