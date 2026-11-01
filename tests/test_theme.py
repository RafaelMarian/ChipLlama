from fastapi.testclient import TestClient
from chipllama.server import create_app
from chipllama.config import Settings

def test_theme_assets_served():
    c = TestClient(create_app(Settings()))
    for f in ("theme.css", "theme.js"):
        assert c.get(f"/static/{f}").status_code == 200
    h = c.get("/").text
    assert "cl_theme" in h and "theme.css" in h and 'id="thbtn"' in h
    css = c.get("/static/theme.css").text
    assert "html[data-theme=light]" in css and "#840000" in css
