"""The visual fixture uses real inventory/readiness without public catalog HTTP."""

from __future__ import annotations

import asyncio
import json
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import yaml


def test_visual_catalog_is_offline_except_for_real_loopback_model_discovery(tmp_path, monkeypatch):
    fixture_path = Path(__file__).resolve().parents[2] / "apps/desktop/e2e/isolated-model-catalog.json"
    # The JSON fixture is shared with the desktop seeder; no production resolver is stubbed.
    fixture = json.loads(fixture_path.read_text())
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("MOCK_API_KEY", "e2e-mock-key")
    (home / "models_dev_cache.json").write_text(json.dumps(fixture["registry"]))
    (home / "cache").mkdir()
    (home / "cache/model_catalog.json").write_text(json.dumps(fixture["manifest"]))
    paths: list[str] = []

    class ModelCatalog(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            body = json.dumps({"object": "list", "data": [{"id": "mock-model", "object": "model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelCatalog)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/v1"
    config = {
        "model": {"provider": "mock", "default": "mock-model"},
        "providers": {"mock": {
            "api": url, "name": "Mock", "api_mode": "chat_completions",
            "key_env": "MOCK_API_KEY", "models": {"mock-model": {}},
        }},
        **fixture["config"],
    }
    (home / "config.yaml").write_text(yaml.safe_dump(config))
    external: list[tuple] = []
    getaddrinfo = socket.getaddrinfo
    connect = socket.socket.connect

    def check_address(address):
        if address[:2] != ("127.0.0.1", server.server_port):
            external.append(address)
            raise AssertionError(f"Visual fixture attempted external metadata connection: {address}")

    def loopback_dns(host, port, *args, **kwargs):
        check_address((host, port))
        return getaddrinfo(host, port, *args, **kwargs)

    def loopback_connect(sock, address):
        check_address(address)
        return connect(sock, address)

    # Guard DNS as well as connect so requests, urllib and custom HTTP openers
    # cannot quietly swallow a blocked public request and still pass the test.
    monkeypatch.setattr(socket, "getaddrinfo", loopback_dns)
    monkeypatch.setattr(socket.socket, "connect", loopback_connect)

    try:
        from agent import models_dev
        from eidolon_cli import inventory, model_catalog
        from eidolon_cli.web_routers.models import get_model_options

        monkeypatch.setattr(models_dev, "_models_dev_cache", {})
        monkeypatch.setattr(models_dev, "_models_dev_cache_time", 0)
        monkeypatch.setattr(models_dev, "_models_dev_retry_after", 0)
        monkeypatch.setattr(model_catalog, "_catalog_cache", None)
        monkeypatch.setattr(model_catalog, "_catalog_cache_source_mtime", 0)
        result = asyncio.run(get_model_options(explicit_only=True))
        with inventory._pricing_prewarm_lock:
            pricing_workers = list(inventory._pricing_prewarm_threads.values())
        for worker in pricing_workers:
            worker.join(timeout=5)
            assert not worker.is_alive()

        assert external == []
        assert paths == ["/v1/models"]
        assert result["provider"] == "mock"
        assert result["model"] == "mock-model"
        row = next(row for row in result["providers"] if row["slug"] == "mock")
        assert row["models"] == ["mock-model"]
        assert row["authenticated"] is True
        assert row["api_url"] == url
        assert "opencode-free" not in {row["slug"] for row in result["providers"]}
        assert models_dev._models_dev_cache == fixture["registry"]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
