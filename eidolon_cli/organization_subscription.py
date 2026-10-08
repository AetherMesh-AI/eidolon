"""Credential-free readiness for subscription-backed organization execution.

This is an admission diagnostic, not a provider, login flow or entitlement probe.
Keep the organization blocked until both execution and usage boundaries exist.
"""
from __future__ import annotations

import json


APP_SERVER_BLOCKER = (
    "Codex app-server cannot enforce the organization tool boundary: native shell, "
    "MCP and child-agent execution can bypass organization grants. Read-only sandbox "
    "and approval settings do not establish a tool-free transport. "
    "Subscription organization execution is unavailable; ordinary Codex chats are unaffected."
)
PLAN_RESPONSES_BLOCKER = (
    "ChatGPT-plan Responses currently does not support max_output_tokens, so it cannot "
    "enforce the organization's reserved output-token ceiling. Stopping a stream is "
    "not a provider usage cap. A dedicated Eidolon authorization is also required."
)


def subscription_readiness() -> dict:
    """Report documented blockers without reading configuration or credentials."""
    return {
        "status": "blocked",
        "checkedAt": "2026-10-08",
        "basis": "documented_capabilities_not_account_probe",
        "providerCalls": 0,
        "credentialAccessed": False,
        "routes": [
            {"transport": "codex_app_server", "status": "blocked",
             "reason": APP_SERVER_BLOCKER, "blocker": "unmediated_native_tools"},
            {"transport": "chatgpt_plan_responses", "status": "blocked",
             "reason": PLAN_RESPONSES_BLOCKER, "blocker": "unsupported_output_token_ceiling",
             "endpoint": "https://api.openai.com/v1/responses",
             "authorization": "dedicated_eidolon_grant_required",
             "implemented": False},
        ],
        "usage": {
            "category": "subscription_allowance",
            "remaining": None,
            "costUsd": None,
            "hardOutputTokenCeilingSupported": False,
            "limitation": "Subscription allowance is account-dependent and is not free or unlimited usage.",
        },
        "documentation": [
            "https://developers.openai.com/codex/app-server",
            "https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations",
            "https://developers.openai.com/siwc/token-sharing-open-source/sign-in",
        ],
    }


if __name__ == "__main__":
    print(json.dumps(subscription_readiness(), indent=2))
