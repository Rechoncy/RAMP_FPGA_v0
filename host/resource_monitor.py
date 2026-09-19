"""Coalesced background resource checks; never uses the serial connection."""
import copy
import threading
from collections import OrderedDict
from PySide6.QtCore import QObject, Signal, QRunnable, QThreadPool, QTimer
from .model import fingerprint
from .resource_usage import assess_resources
from .compiler_control import compilation_scope,CompilationCancelled


class _Signals(QObject):
    done = Signal(object, object)


class _Check(QRunnable):
    def __init__(self, key, profile, caps, signals,cancel):
        super().__init__();self.key=key;self.profile=profile;self.caps=caps;self.signals=signals;self.cancel=cancel

    def run(self):
        try:
            with compilation_scope(self.cancel):report=assess_resources(self.profile,self.caps)
        except CompilationCancelled:report=None
        self.signals.done.emit(self.key,report)


class ResourceMonitor(QObject):
    changed=Signal()
    stopped=Signal()

    def __init__(self,parent=None):
        super().__init__(parent)
        self.cache=OrderedDict();self.wanted={};self.jobs=OrderedDict();self.running=None;self.closed=False
        self.cancel=None
        self.pool=QThreadPool(self);self.pool.setMaxThreadCount(1)
        self.signals=_Signals(self);self.signals.done.connect(self.finish)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(450);self.timer.timeout.connect(self.start_next)

    def request(self,slot,profile,caps):
        if self.closed:return None
        if profile is None:
            self.wanted.pop(slot,None);self.prune();return None
        key=(fingerprint(profile),caps)
        previous=self.wanted.get(slot);self.wanted[slot]=key
        if key in self.cache:
            self.cache.move_to_end(key);self.prune();return self.cache[key]
        if key!=self.running and key not in self.jobs:self.jobs[key]=(copy.deepcopy(profile),caps)
        self.prune()
        if previous!=key or not self.timer.isActive():
            if self.running is None:self.timer.start()
        return None

    def prune(self):
        wanted=set(self.wanted.values())
        if self.running is not None and self.running not in wanted:self.cancel.set()
        for key in list(self.jobs):
            if key not in wanted:self.jobs.pop(key)

    def start_next(self):
        if self.closed or self.running is not None:return
        self.prune()
        if not self.jobs:return
        key,(profile,caps)=self.jobs.popitem(last=False);self.running=key
        self.cancel=threading.Event();self.pool.start(_Check(key,profile,caps,self.signals,self.cancel))

    def remember(self,report):
        key=(report.profile_fingerprint,report.capabilities)
        self.cache[key]=report;self.cache.move_to_end(key)
        while len(self.cache)>32:self.cache.popitem(last=False)
        self.jobs.pop(key,None)

    def finish(self,key,report):
        self.running=None;self.cancel=None
        if self.closed:self.stopped.emit();return
        if report is not None:self.remember(report)
        elif key in self.wanted.values():
            # A -> B -> A edits may restore a key after cancellation started.
            from .model import Profile
            import json
            self.jobs[key]=(Profile.from_dict(json.loads(key[0])),key[1])
        if key in self.wanted.values():self.changed.emit()
        if self.jobs:self.timer.start(0)

    def begin_close(self):
        self.closed=True;self.timer.stop();self.jobs.clear();self.wanted.clear()
        if self.cancel:self.cancel.set()
        self.pool.clear()
        return self.running is None or self.pool.activeThreadCount()==0

    def close(self):
        self.begin_close()
        # At most one bounded compiler pass remains. Keep its signal owner alive
        # until it finishes instead of destroying a running Qt worker.
        self.pool.waitForDone()
