from pathlib import Path
from app.build_info import capture


def test_identity_and_snapshot(tmp_path,monkeypatch):
    monkeypatch.setenv('GIT_CEILING_DIRECTORIES',str(tmp_path.parent))
    (tmp_path / "app").mkdir()
    (tmp_path / "static").mkdir()
    source = tmp_path / "app/demo.py"
    source.write_text("VALUE=1")
    asset = tmp_path / "static/index.html"
    asset.write_text("old")
    first, assets = capture(tmp_path)
    same, _ = capture(tmp_path)
    assert first["build_id"] == same["build_id"]
    source.write_text("VALUE=2")
    changed, _ = capture(tmp_path)
    assert changed["build_id"] != first["build_id"]
    asset.write_text("new")
    assert assets["index.html"] == b"old"
    assert first["commit"] is None
    assert first["acceptance_version"] == "R022"


def test_served_build_matches_html(monkeypatch):
    monkeypatch.setenv("MX_TESTING", "1")
    from fastapi.testclient import TestClient
    from app.main import app, BUILD_INFO
    client = TestClient(app)
    response = client.get("/api/build")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["build_id"] == BUILD_INFO["build_id"]
    html = client.get("/")
    assert BUILD_INFO["build_id"] in html.text
    assert "copy-test-info" in html.text
    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert js.headers["cache-control"] == "no-store"
    assert "navigator.clipboard.writeText" in js.text
    assert client.get("/static/missing.js").status_code == 404
