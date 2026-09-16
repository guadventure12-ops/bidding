"""Keep one server owner per database, before application startup touches jobs."""
from contextlib import contextmanager
import os
from pathlib import Path


class AlreadyRunning(RuntimeError):
    pass


@contextmanager
def instance_lock(data_directory):
    directory = Path(data_directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'service-instance.lock').open('a+b') as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AlreadyRunning('This bidding workspace database is already in use by another server.') from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
