from fastapi.testclient import TestClient

from app.main import app


def test_static_assets_use_proxy_safe_relative_urls():
    response = TestClient(app, base_url="http://internal-container").get(
        "/", headers={"x-forwarded-proto": "https", "host": "dhl.rghome.arpa"}
    )

    assert response.status_code == 200
    assert 'href="/static/style.css"' in response.text
    assert 'src="/static/dhl-logo.svg"' in response.text
    assert "http://internal-container/static/" not in response.text


def test_stylesheet_is_served_as_css():
    response = TestClient(app).get("/static/style.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
