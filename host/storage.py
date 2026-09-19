from pathlib import Path
import json
import os
import datetime
from .model import Profile, presets
from .runtime import data_dir


class Library:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else data_dir()
        self.profiles = self.root / "profiles"
        self.profiles.mkdir(parents=True, exist_ok=True)
        self.errors = []
        # An initialized library may intentionally be empty after archiving or
        # a global restore. Seed examples only for a new workspace.
        if not any(self.profiles.glob("*.json")) and not (self.root / '.sample_profiles_added').exists():
            for p in presets():
                self.save(p)

    def save(self, profile):
        # Never trust imported IDs as file paths.
        if len(profile.id) != 32 or any(c not in "0123456789abcdef" for c in profile.id):
            raise ValueError("无效的方案 ID")
        path = self.profiles / f"{profile.id}.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        os.replace(temp, path)

    def list(self):
        result = []
        self.errors = []
        for path in self.profiles.glob("*.json"):
            try:
                result.append(Profile.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (ValueError, KeyError, TypeError) as exc:
                self.errors.append(f"{path.name}: {exc}")
        # Add new editor examples once without modifying any existing experiment.
        marker=self.root / '.sample_profiles_added'
        if not marker.exists():
            if not any(p.mode in ('ramp','function') for p in result):
                for p in presets()[:2]: self.save(p); result.append(p)
            marker.touch()
        return sorted(result, key=lambda p: p.name)

    def archive(self, profile):
        path = self.profiles / f"{profile.id}.json"
        destination = self.root / "archive"
        destination.mkdir(exist_ok=True)
        if path.exists():
            path.replace(destination / f"{profile.id}_{datetime.datetime.now():%Y%m%d_%H%M%S_%f}.json")

    def log(self, event, **fields):
        folder = self.root / "logs"
        folder.mkdir(exist_ok=True)
        now = datetime.datetime.now().astimezone()
        with (folder / f"{now:%Y-%m-%d}.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(time=now.isoformat(), event=event, **fields), ensure_ascii=False, allow_nan=False) + "\n")
