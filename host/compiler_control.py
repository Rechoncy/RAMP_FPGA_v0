"""Optional cooperative cancellation for disposable background compilation.

    with compilation_scope(cancel_event):
        compile_samples(profile)

An unset scope is the default for uploads and all existing callers. ContextVar
state is local to the executing thread/context and restored even on failure.
Cancelled computations raise before publishing a partial result; functools
caches only completed return values, so no incomplete waveform can be reused.
"""
from contextlib import contextmanager
from contextvars import ContextVar


class CompilationCancelled(Exception):
    """Control flow for an obsolete background job, not a waveform error."""


_cancel_event = ContextVar('ramp_compiler_cancel_event', default=None)


@contextmanager
def compilation_scope(cancel_event):
    token = _cancel_event.set(cancel_event)
    try:
        check_cancelled()
        yield
    finally:
        _cancel_event.reset(token)


def check_cancelled():
    event = _cancel_event.get()
    if event is not None and event.is_set():
        raise CompilationCancelled('后台资源检查已取消')
