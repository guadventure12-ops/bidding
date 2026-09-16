"""Exercise actual child processes against an isolated database and script mocks."""
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import time

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def test_second_server_cannot_interrupt_jobs_in_the_same_database(tmp_path):
    env = {**os.environ, 'MX_DATA_DIR': str(tmp_path / 'data'), 'BIDDING_PORT': str(free_port())}
    env.pop('DEEPSEEK_API_KEY', None)
    env.pop('MX_TESTING', None)
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    children = []
    try:
        first = subprocess.Popen([sys.executable, str(ROOT / 'run.py')], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags)
        children.append(first)
        with httpx.Client(trust_env=False, timeout=1) as client:
            for _ in range(80):
                try:
                    if client.get('http://127.0.0.1:' + env['BIDDING_PORT'] + '/api/health').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else:
                pytest.fail('Isolated server did not start')
        db_path = tmp_path / 'data/bidding.sqlite3'
        with sqlite3.connect(db_path) as conn:
            conn.execute("INSERT INTO jobs(id,mode,status,created_at) VALUES('startup-marker','import','running','test')")
        second = subprocess.Popen([sys.executable, str(ROOT / 'run.py')], env={**env, 'BIDDING_PORT': str(free_port())}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags)
        children.append(second)
        try:
            second.communicate(timeout=4)
        except subprocess.TimeoutExpired:
            pytest.fail('Second server opened the same database instead of refusing duplicate startup')
        assert second.returncode != 0
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT status FROM jobs WHERE id='startup-marker'").fetchone()[0] == 'running'
        assert first.poll() is None
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
            child.communicate(timeout=10)


@pytest.mark.skipif(os.name != 'nt', reason='Windows launcher')
def test_launcher_rejects_unrelated_service_on_its_port(tmp_path):
    shell = shutil.which('pwsh') or shutil.which('powershell')
    assert shell
    shutil.copy2(ROOT / 'Start.ps1', tmp_path / 'Start.ps1')
    test_script = tmp_path / 'probe.ps1'
    test_script.write_text('''
function Invoke-RestMethod { return @{ok=$true; name='Unrelated service'} }
function Start-Process { throw 'Unexpected launch must not replace unrelated service' }
try {
    & (Join-Path $PSScriptRoot 'Start.ps1') -NoBrowser
    exit 19
} catch {
    if ($_.Exception.Message -match 'another service|其他服务') { exit 0 }
    Write-Output $_.Exception.Message
    exit 20
}
''', encoding='utf-8-sig')
    result = subprocess.run([shell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(test_script)], capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stdout.decode(errors='replace')
