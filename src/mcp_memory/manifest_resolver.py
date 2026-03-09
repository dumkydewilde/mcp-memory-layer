"""Manifest resolver — fetches dbt manifest from local file or URL."""

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_DIR = Path.home() / ".mcp-memory" / "cache"


def resolve_manifest(source: str, cache_dir: Path = CACHE_DIR) -> dict | None:
    """Resolve a dbt manifest from a source string.

    Supported sources:
        - Local file path: /path/to/manifest.json
        - HTTP(S) URL: https://bucket.s3.amazonaws.com/manifest.json

    Args:
        source: Manifest source — file path or URL.
        cache_dir: Directory for caching remote manifests.

    Returns:
        Parsed manifest dict, or None if the source is unavailable.
    """
    source = source.strip()

    if source.startswith(("http://", "https://")):
        return _resolve_url(source, cache_dir)
    else:
        return _resolve_local(source)


def _resolve_local(path_str: str) -> dict | None:
    """Load manifest from a local file path."""
    path = Path(path_str).expanduser()
    if not path.exists():
        logger.warning("Manifest not found at %s", path)
        return None
    logger.info("Loading manifest from %s", path)
    return json.loads(path.read_text())


def _resolve_url(url: str, cache_dir: Path) -> dict | None:
    """Fetch manifest from an HTTP(S) URL with local caching."""
    from urllib.error import URLError
    from urllib.request import Request, urlopen

    cache_file = cache_dir / "manifest.json"
    cache_meta = cache_dir / "manifest.meta.json"

    # Check cache freshness
    if cache_file.exists() and cache_meta.exists():
        meta = json.loads(cache_meta.read_text())
        age_hours = (time.time() - meta.get("fetched_at", 0)) / 3600
        if age_hours < 1:
            logger.info("Using cached manifest (%.0f min old)", age_hours * 60)
            return json.loads(cache_file.read_text())

    # Fetch
    logger.info("Fetching manifest from %s", url)
    try:
        req = Request(url, headers={"User-Agent": "mcp-memory"})
        with urlopen(req, timeout=30) as resp:
            data = resp.read()
    except (URLError, OSError) as e:
        logger.warning("Failed to fetch manifest from %s: %s", url, e)
        # Fall back to cache if available
        if cache_file.exists():
            logger.info("Using stale cached manifest")
            return json.loads(cache_file.read_text())
        return None

    # Cache it
    manifest = json.loads(data)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(data.decode())
    cache_meta.write_text(json.dumps({"source": url, "fetched_at": time.time()}))
    logger.info("Cached manifest (%d nodes)", len(manifest.get("nodes", {})))
    return manifest
