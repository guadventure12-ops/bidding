"""Offline render lifecycle checks: subprocess calls are mocked; no Word starts."""
import base64
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from app import documents


@pytest.fixture
def render_environment(tmp_path, monkeypatch):
    source = tmp_path / 'bid.docx'
    source.write_bytes(b'unchanged source DOCX')
    target = tmp_path / 'bid.pdf'
    system = tmp_path / 'windows'
    powershell = system / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    powershell.parent.mkdir(parents=True)
    powershell.touch()
    monkeypatch.setenv('SystemRoot', str(system))
    monkeypatch.setattr(documents.os, 'name', 'nt')
    monkeypatch.setattr(documents, 'Path', type(tmp_path))
    return source, target


@pytest.mark.parametrize('outcome', ['timeout', 'failed', 'success'])
def test_render_cleanup_permission_does_not_mask_timeout_failure_or_success(render_environment, tmp_path, monkeypatch, outcome):
    source, target = render_environment
    workspace = tmp_path / 'owned-workspace'
    workspace.mkdir()
    class LockedWorkspace:
        name = str(workspace)
        def __init__(self, **kwargs):
            pass
        def cleanup(self):
            raise PermissionError('Word has not released temporary file')
    monkeypatch.setattr(documents.tempfile, 'TemporaryDirectory', LockedWorkspace)
    calls = []
    def run(command, **kwargs):
        calls.append(kwargs)
        assert kwargs['timeout'] == 600
        assert kwargs['creationflags'] == getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        script = base64.b64decode(command[-1]).decode('utf-16le')
        assert script.index('Set-Content -LiteralPath $env:BIDDING_RENDER_OWNER') < script.index('$word.Documents.Open')
        assert '[intptr]$word.Hwnd' not in script
        assert script.index('$word.Documents.Add()') < script.index('[intptr]$document.ActiveWindow.Hwnd') < script.index('Set-Content -LiteralPath $env:BIDDING_RENDER_OWNER')
        assert script.index('$document.Close([ref]0)') < script.index('$word.Documents.Open')
        if outcome == 'timeout':
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        if outcome == 'success':
            Path(kwargs['env']['BIDDING_RENDER_OUTPUT']).write_bytes(b'%PDF-' + b'x' * 200)
        return SimpleNamespace(returncode=0 if outcome == 'success' else 1, stdout=b'', stderr=b'original render failure')
    monkeypatch.setattr(documents.subprocess, 'run', run)
    result = documents.render_pdf(str(source), str(target))
    assert len(calls) == 1 and source.read_bytes() == b'unchanged source DOCX'
    assert any('PermissionError' in warning and '临时目录' in warning for warning in result['warnings'])
    if outcome == 'timeout':
        assert result['path'] is None and result['error_type'] == 'timeout' and result['timeout_seconds'] == 600
        assert '超过 600 秒' in result['warnings'][0] and not target.exists()
        assert result['owned_process_cleanup'] == 'owner_record_unavailable'
    elif outcome == 'failed':
        assert result['path'] is None and result['error_type'] == 'render_failed'
        assert 'original render failure' in result['warnings'][0] and not target.exists()
    else:
        assert result['path'] == str(target) and target.read_bytes().startswith(b'%PDF-')


@pytest.mark.parametrize('record', ['', '1', '1,2', '0,639246163381522739', '26764,wrong', '26764,639246163381522739,extra', '2147483648,639246163381522739'])
def test_invalid_owner_record_never_launches_process_cleanup(tmp_path, monkeypatch, record):
    owner = tmp_path / 'owned-word.txt'
    owner.write_text(record, encoding='ascii')
    monkeypatch.setattr(documents.subprocess, 'run', lambda *a, **k: pytest.fail('Invalid owner must never dispatch process termination'))
    assert documents._stop_owned_render_word(Path('powershell.exe'), owner) == 'owner_record_invalid'


def test_timeout_cleanup_uses_recorded_identity_and_keeps_target_unpublished(render_environment, monkeypatch):
    source, target = render_environment
    calls = []
    def run(command, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            Path(kwargs['env']['BIDDING_RENDER_OWNER']).write_text('26764,639246163381522739', encoding='ascii')
            Path(kwargs['env']['BIDDING_RENDER_OUTPUT']).write_bytes(b'%PDF-partial')
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        assert kwargs['env']['BIDDING_RENDER_IDENTITY'] == '26764,639246163381522739'
        script = base64.b64decode(command[-1]).decode('utf-16le')
        # Cleanup must be exact-identity guarded, never an image-name sweep.
        assert "ProcessName -ne 'WINWORD'" in script and 'StartTime.ToUniversalTime().Ticks.ToString() -ne $record[1]' in script
        assert 'Stop-Process -InputObject $owned' in script and 'Get-Process -Name' not in script
        assert kwargs['timeout'] <= 15
        return SimpleNamespace(returncode=0, stdout=b'identity_mismatch\r\n', stderr=b'')
    monkeypatch.setattr(documents.subprocess, 'run', run)
    result = documents.render_pdf(str(source), str(target))
    assert len(calls) == 2 and result['error_type'] == 'timeout'
    assert result['owned_process_cleanup'] == 'identity_mismatch' and not target.exists()
    assert any('未终止任何无法核验归属' in warning for warning in result['warnings'])
