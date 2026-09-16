"""Offline checks of an empty public installation; all values are synthetic."""
from fastapi.testclient import TestClient

from app import db, provider
from app.main import app


def test_empty_installation_has_no_company_data_or_personal_paths():
    settings = db.get_settings()
    assert settings['company_name'] == ''
    assert settings['knowledge_path'] == ''
    assert settings['tender_path'] == ''
    assert db.all('SELECT * FROM projects') == []
    assert db.all('SELECT * FROM documents') == []
    assert not provider.key_configured()
    assert (db.DATA / 'bidding.sqlite3').is_file()


def test_public_identity_and_local_response_security_headers():
    with TestClient(app) as client:
        response = client.get('/api/health')
        assert response.status_code == 200
        assert response.json()['app_id'] == 'bidding-local'
        assert response.json()['name'] == '招投标'
        assert response.json()['local_only'] is True
        assert response.json()['key_configured'] is False
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['x-frame-options'] == 'DENY'
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert "connect-src 'self'" in response.headers['content-security-policy']
        assert client.get('/api/health', headers={'host': 'public.example'}).status_code == 403
        assert client.get('/api/settings', headers={'origin': 'https://public.example'}).status_code == 403


def test_settings_never_returns_synthetic_encrypted_key():
    db.set_setting('api_key_encrypted', 'synthetic-ciphertext-only')
    with TestClient(app) as client:
        response = client.get('/api/settings')
        assert response.status_code == 200
        assert response.json()['key_configured'] is True
        assert 'synthetic-ciphertext-only' not in response.text
        assert 'api_key_encrypted' not in response.text
