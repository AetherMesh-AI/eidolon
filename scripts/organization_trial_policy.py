"""No-cost sandbox admission and explicit, bounded live trial pricing policy.

Prices are owner-selected conservative ceilings, never inferred current prices.
Actual provider fees may differ; this admission budget is not an invoice guarantee.
No provider clients or credential discovery are needed to import or preflight.
"""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


COST_LIMITATION = (
    "USD admission reservations use the explicitly selected exact provider/model input and output "
    "per-million ceilings. Every physical send, including retries and interrupted calls, retains "
    "its reservation. Actual provider fees may differ from those ceilings; this is not an invoice guarantee."
)


def add_live_arguments(parser):
    parser.add_argument("--live-model", action="store_true", help=(
        "Use the explicitly selected real model only on disposable evaluation projects."))
    parser.add_argument("--acknowledge-billing", action="store_true", help=(
        "Acknowledge provider charges and the limits of configured price ceilings."))
    parser.add_argument("--provider-config", type=Path, help=(
        "Credential-free JSON selecting provider, model, base_url, api_mode, key_env and one exact model_costs row."))
    parser.add_argument("--max-cost-usd", help=(
        "Required live USD reservation cap, using the explicitly configured input/output price ceilings; not an invoice guarantee."))
    return parser


def selected_live_route(args):
    """Validate only deliberate nonsecret choices; do not access credentials."""
    live = bool(args.live_model)
    choices = (args.acknowledge_billing, args.provider_config, args.max_cost_usd)
    if not live:
        if any(value is not None and value is not False for value in choices):
            raise ValueError("--provider-config, --acknowledge-billing and --max-cost-usd require --live-model")
        return None
    if not all(choices):
        raise ValueError("--live-model requires --acknowledge-billing, --provider-config and --max-cost-usd")
    from eidolon_cli.organization_budget import money, parse_model_costs

    cap = money(args.max_cost_usd, "--max-cost-usd", allow_zero=False)
    raw = json.loads(args.provider_config.read_text(encoding="utf-8"))
    text_fields = {"provider", "model", "base_url", "api_mode", "key_env"}
    if not isinstance(raw, dict) or set(raw) != text_fields | {"model_costs"}:
        raise ValueError("Provider JSON requires exactly provider, model, base_url, api_mode, key_env and model_costs; no secrets")
    if any(not isinstance(raw[key], str) or not raw[key] or raw[key] != raw[key].strip()
           or len(raw[key]) > 300 or any(ord(char) < 32 or ord(char) == 127 for char in raw[key])
           for key in text_fields):
        raise ValueError("Provider settings must be bounded nonempty canonical strings")
    if raw["provider"] != "custom" or raw["api_mode"] != "chat_completions":
        raise ValueError("This evaluation supports only an explicit custom direct chat_completions API route")
    endpoint = urlsplit(raw["base_url"])
    if (endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password
            or endpoint.query or endpoint.fragment):
        raise ValueError("Live base_url must be an explicit HTTPS endpoint without credentials, query or fragment")
    # Validate malformed ports here, before credentials or a provider client exist.
    _ = endpoint.port
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", raw["key_env"]):
        raise ValueError("key_env must name one existing credential variable")
    rates = parse_model_costs(raw["model_costs"])
    if len(rates) != 1 or (rates[0].provider, rates[0].model) != (raw["provider"], raw["model"]):
        raise ValueError("model_costs must contain exactly the selected provider/model input and output ceilings")
    return {**raw, "base_url": raw["base_url"].rstrip("/"), "max_cost_usd": cap,
            "model_costs": [asdict(rates[0])]}


def native_preflight():
    """Prove the existing sandbox with trusted code before spending or reading keys."""
    from eidolon_cli.organization_project_runner import probe_project_runner

    # The existing probe supplies its trusted tiny snapshot to run_project_tests.
    # It has no provider, profile, credential, or generated-project dependency.
    receipt = probe_project_runner()
    passed = (receipt["status"] == "passed" and receipt["exitCode"] == 0
              and receipt["testCount"] == 1 and receipt["skippedCount"] == 0
              and receipt["isolation"]["established"] is True)
    outcome = "passed" if passed else "unsupported" if receipt["status"] == "unsupported" else "failed"
    return {"outcome": outcome, "providerCalls": 0, "credentialAccessed": False,
            "execution": receipt, "limitations": "Sandbox preflight only; no model capability was evaluated."}


def load_live_secret(route, preflight):
    """Read the one selected key only after a successful real native preflight."""
    execution = preflight.get("execution", {})
    if (preflight.get("outcome") != "passed" or execution.get("status") != "passed"
            or execution.get("isolation", {}).get("established") is not True):
        raise ValueError("Native sandbox preflight must pass before any credential access or provider initialization")
    from agent.secret_scope import get_secret, UnscopedSecretError
    from eidolon_cli.auth import has_usable_secret

    try:
        secret = get_secret(route["key_env"])
    except UnscopedSecretError:
        raise ValueError("No current credential scope is available; implicit profile discovery is disabled") from None
    if not isinstance(secret, str) or not has_usable_secret(secret):
        raise ValueError("The explicitly selected credential is unavailable in the current secret scope")
    return {**route, "secret": secret}


def budget_configuration(route):
    """Use the existing durable, exact-route physical-send reservation machinery."""
    if "max_cost_usd" not in route and "model_costs" not in route:
        endpoint = urlsplit(route.get("base_url", ""))
        if endpoint.scheme == "http" and endpoint.hostname in {"127.0.0.1", "::1"}:
            return {}  # Nonbillable synthetic routes need no guessed dollar pricing.
        raise ValueError("Live routes require explicit max_cost_usd and exact model_costs; no unlimited fallback")
    from eidolon_cli.organization_budget import money, parse_model_costs

    cap = money(route.get("max_cost_usd"), "max_cost_usd", allow_zero=False)
    rates = parse_model_costs(route.get("model_costs"))
    if len(rates) != 1 or (rates[0].provider, rates[0].model) != ("custom", route["model"]):
        raise ValueError("Live cost policy must cover the exact custom provider/model")
    return {"max_cost_usd": cap, "model_costs": [asdict(rates[0])]}
