"""Tests for multi-source manifest resolver."""

import json
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from threading import Thread

import pytest

from mcp_memory.manifest_resolver import resolve_manifest

SAMPLE_MANIFEST = {
    "nodes": {
        "model.test.orders": {
            "resource_type": "model",
            "name": "orders",
            "description": "All orders",
            "columns": {},
            "depends_on": {"nodes": []},
            "raw_code": "SELECT * FROM raw_orders",
            "config": {"materialized": "table"},
        }
    }
}


def test_resolve_local_file(tmp_path):
    """Resolves manifest from a local file path."""
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(SAMPLE_MANIFEST))

    result = resolve_manifest(str(manifest_file))
    assert result is not None
    assert "model.test.orders" in result["nodes"]


def test_resolve_local_missing():
    """Returns None for a nonexistent local file."""
    result = resolve_manifest("/nonexistent/manifest.json")
    assert result is None


def test_resolve_local_expanduser(tmp_path, monkeypatch):
    """Expands ~ in local paths."""
    monkeypatch.setenv("HOME", str(tmp_path))
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(SAMPLE_MANIFEST))

    result = resolve_manifest("~/manifest.json")
    assert result is not None


def test_resolve_url(tmp_path):
    """Fetches manifest from an HTTP URL."""
    # Write manifest to a temp dir for serving
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(SAMPLE_MANIFEST))

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(tmp_path), **kwargs)

        def log_message(self, format, *args):
            pass  # suppress output

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        cache_dir = tmp_path / "cache"
        result = resolve_manifest(f"http://127.0.0.1:{port}/manifest.json", cache_dir)
        assert result is not None
        assert "model.test.orders" in result["nodes"]

        # Verify it was cached
        assert (cache_dir / "manifest.json").exists()
        assert (cache_dir / "manifest.meta.json").exists()
    finally:
        server.shutdown()


def test_resolve_url_uses_cache(tmp_path):
    """Returns cached manifest when fresh enough."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "manifest.json").write_text(json.dumps(SAMPLE_MANIFEST))
    (cache_dir / "manifest.meta.json").write_text(
        json.dumps({"source": "http://example.com/m.json", "fetched_at": time.time()})
    )

    # URL that would fail — proves we're using the cache
    result = resolve_manifest("http://127.0.0.1:1/manifest.json", cache_dir)
    assert result is not None
    assert "model.test.orders" in result["nodes"]


def test_resolve_url_fallback_to_stale_cache(tmp_path):
    """Falls back to stale cache when URL is unreachable."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "manifest.json").write_text(json.dumps(SAMPLE_MANIFEST))
    (cache_dir / "manifest.meta.json").write_text(
        json.dumps({"source": "http://example.com/m.json", "fetched_at": 0})  # very stale
    )

    result = resolve_manifest("http://127.0.0.1:1/manifest.json", cache_dir)
    assert result is not None


def test_resolve_url_no_cache_unreachable(tmp_path):
    """Returns None when URL is unreachable and no cache exists."""
    cache_dir = tmp_path / "cache"
    result = resolve_manifest("http://127.0.0.1:1/manifest.json", cache_dir)
    assert result is None
