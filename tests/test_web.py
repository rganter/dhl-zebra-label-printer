from fastapi.testclient import TestClient

import time

from app import main
from app.label_processor import ProcessedLabel

app = main.app


def test_static_assets_use_proxy_safe_relative_urls():
    response = TestClient(app, base_url="http://internal-container").get(
        "/", headers={"x-forwarded-proto": "https", "host": "dhl.rghome.arpa"}
    )

    assert response.status_code == 200
    assert 'href="/static/style.css"' in response.text
    assert 'src="/static/dhl-logo.svg"' in response.text
    assert "http://internal-container/static/" not in response.text
    assert response.headers["cache-control"] == "no-store, max-age=0"


def test_stylesheet_is_served_as_css():
    response = TestClient(app).get("/static/style.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")


def test_lifespan_clears_in_memory_labels_on_restart():
    main._labels["sensitive"] = main.CachedLabel(
        time.monotonic(), ProcessedLabel(b"unused", (0, 0, 1, 1))
    )

    with TestClient(app):
        assert main._labels == {}

    assert main._labels == {}
