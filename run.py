"""Run the local bidding workbench with dependencies from the active venv."""
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

if __name__ == '__main__':
    from app.server_lifecycle import AlreadyRunning, instance_lock
    try:
        port = int(os.environ.get('BIDDING_PORT', '8765'))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise SystemExit('BIDDING_PORT must be an integer between 1 and 65535.')
    try:
        with instance_lock(os.environ.get('MX_DATA_DIR') or ROOT / 'data'):
            import uvicorn
            uvicorn.run('app.main:app', host='127.0.0.1', port=port, log_level='info')
    except AlreadyRunning as exc:
        raise SystemExit(str(exc))
