"""Bounded FD reads and no-clobber publication on the same filesystem."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import tempfile

from .formats import AuditError, MAX_JSON, bounded, parse


def read_bytes(path, limit=MAX_JSON):
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise AuditError('unsafe_input')
        if before.st_size > limit:
            raise AuditError('limit_exceeded')
        parts, total = [], 0
        while True:
            part = os.read(fd, min(65536, limit + 1 - total))
            if not part:
                break
            parts.append(part)
            total += len(part)
            if total > limit:
                raise AuditError('limit_exceeded')
        after, named = os.fstat(fd), os.lstat(path)
        stamp = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if stamp(before) != stamp(after) or stamp(before) != stamp(named):
            raise AuditError('input_changed')
        return b''.join(parts)
    except OSError as exc:
        raise AuditError('input_io_error') from exc
    finally:
        if fd is not None:
            os.close(fd)


def read_json(path, limit=MAX_JSON):
    return parse(read_bytes(path, limit), limit)


def sync_directory(parent):
    fd = os.open(parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def new_target(target):
    target = Path(target)
    temp = None
    try:
        if os.path.lexists(target):
            raise AuditError('output_exists')
        fd, name = tempfile.mkstemp(prefix='.audit-', suffix='.tmp', dir=target.parent)
        os.close(fd)
        temp = Path(name)
        yield temp
        # A caller must close SQLite and remove its rollback journal normally.
        if any(os.path.lexists(str(temp) + suffix) for suffix in ('-journal', '-wal', '-shm')):
            raise AuditError('storage_error')
        fd = os.open(temp, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.link(temp, target, follow_symlinks=False)
        temp.unlink()
        temp = None
        sync_directory(target.parent)
    except FileExistsError as exc:
        raise AuditError('output_exists') from exc
    except OSError as exc:
        # A final name may already exist if directory fsync failed. Never remove it.
        raise AuditError('output_io_error') from exc
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def write_json(target, value, limit=MAX_JSON):
    data = bounded(value, limit - 1) + b'\n'
    with new_target(target) as temp:
        with temp.open('wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
