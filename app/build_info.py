"""Immutable process-start identity and frontend snapshot; no business data read."""
import hashlib
import mimetypes
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ACCEPTANCE_VERSION = "R022"


def capture(root):
    root = Path(root)
    files = sorted([*root.glob('app/*.py'), *root.glob('static/**/*'),
                    root / 'run.py', root / 'requirements.txt'])
    digest = hashlib.sha256()
    assets = {}
    for path in files:
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        digest.update(relative.encode() + b'\0' + hashlib.sha256(content).digest())
        if relative.startswith('static/'):
            assets[relative[7:]] = content
    def git(*args):
        try:
            return subprocess.check_output(['git', '-C', str(root), *args],
                                           stderr=subprocess.DEVNULL, timeout=3).decode().strip()
        except (OSError, subprocess.SubprocessError):
            return None
    status = git('status', '--porcelain', '--untracked-files=normal')
    info = dict(acceptance_version=ACCEPTANCE_VERSION, build_id=digest.hexdigest(),
                commit=git('rev-parse', 'HEAD'), dirty=None if status is None else bool(status),
                started_at=datetime.now(timezone.utc).isoformat())
    return info, assets


def asset_type(name):
    return {'.js': 'application/javascript', '.css': 'text/css', '.html': 'text/html'}.get(
        Path(name).suffix, mimetypes.guess_type(name)[0] or 'application/octet-stream')
