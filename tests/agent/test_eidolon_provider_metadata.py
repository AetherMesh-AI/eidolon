"""Rebranding changes app attribution, never the provider request contract."""

import json

import httpx
import pytest


@pytest.mark.parametrize("provider,base_url", [
    ("openrouter", "https://openrouter.ai/api/v1"),
    ("ai-gateway", "https://ai-gateway.vercel.sh/v1"),
    ("fireworks", "https://api.fireworks.ai/inference/v1"),
    ("kimi-coding", "https://api.kimi.com/coding/v1"),
    ("opencode-zen", "https://opencode.ai/zen/v1"),
    ("opencode-go", "https://opencode.ai/zen/go/v1"),
    ("opencode-free", "https://opencode.ai/zen/v1"),
])
def test_app_attribution_preserves_request_destination_auth_and_model(monkeypatch, provider, base_url):
    from agent import auxiliary_client as aux

    wire = []
    model = "NousResearch/Hermes-4-70B"

    def respond(request):
        wire.append(request)
        return httpx.Response(200, json={
            "id": "test", "object": "chat.completion", "created": 0, "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                         "finish_reason": "stop"}],
        })

    monkeypatch.setattr(aux, "_openai_http_client_kwargs", lambda *_args, **_kwargs: {
        "http_client": httpx.Client(transport=httpx.MockTransport(respond)),
    })
    headers = aux.build_or_headers({}) if provider == "openrouter" else aux._profile_default_headers(provider)
    client = aux._create_openai_client(api_key="test-only-key", base_url=base_url, default_headers=headers)
    try:
        client.chat.completions.create(model=model, messages=[{"role": "user", "content": "test"}])
    finally:
        client.close()

    request, = wire
    assert str(request.url) == base_url + "/chat/completions"
    assert request.headers["authorization"] == ("" if provider == "opencode-free" else "Bearer test-only-key")
    assert json.loads(request.content)["model"] == model
    assert request.headers["http-referer"] == "https://github.com/AetherMesh-AI/Eidolon"
    assert request.headers["x-title"] == "Eidolon"


@pytest.mark.parametrize("base_url", ["https://opencode.ai/zen/go/v1", "https://api.kimi.com/coding"])
def test_anthropic_wire_keeps_auth_and_compatibility_headers(monkeypatch, base_url):
    import anthropic
    from agent import anthropic_adapter as adapter

    wire = []

    def respond(request):
        wire.append(request)
        return httpx.Response(200, json={
            "id": "test", "type": "message", "role": "assistant", "content": [],
            "model": "vendor-model", "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 0},
        })

    original = adapter._new_sdk_client

    def new_client(sdk, kwargs, headers):
        kwargs["http_client"] = httpx.Client(transport=httpx.MockTransport(respond))
        return original(sdk, kwargs, headers)

    monkeypatch.setattr(adapter, "_anthropic_sdk", anthropic)
    monkeypatch.setattr(adapter, "_new_sdk_client", new_client)
    client = adapter.build_anthropic_client("test-only-key", base_url=base_url)
    try:
        client.messages.create(model="vendor-model", max_tokens=8, messages=[{"role": "user", "content": "test"}])
    finally:
        client.close()

    request, = wire
    assert str(request.url) == base_url.removesuffix("/v1") + "/v1/messages"
    assert request.headers["x-api-key"] == "test-only-key"
    assert "authorization" not in request.headers
    assert "anthropic-beta" in request.headers
    assert json.loads(request.content)["model"] == "vendor-model"
    assert request.headers["http-referer"] == "https://github.com/AetherMesh-AI/Eidolon"
    assert request.headers["x-title"] == "Eidolon"
    assert request.headers["user-agent"].startswith("HermesAgent/")
