"""Shared configuration values without config loading or provider discovery.

Strict policy readers and credential scopes use these primitives without
importing the interactive config module or invoking its recovery behavior.
"""
import logging
import os
import re
import threading
from typing import Any, Dict, Optional, Tuple

from eidolon_cli import managed_scope

# Preserve the existing logger identity for callers filtering config warnings.
logger = logging.getLogger("eidolon_cli.config")


class InvalidUserConfigError(RuntimeError):
    """Raised when a run that cannot repair config finds invalid user YAML."""


# Shared with interactive config readers/writers; no module-local replacement.
_CONFIG_LOCK = threading.RLock()


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*: dict-over-dict recurses (so overriding one leaf
    keeps sibling defaults), and ``None`` over a dict section is ignored.

    An empty section key in config.yaml (``terminal:`` with no value) parses as YAML ``None``; treating that
    as an override would replace the entire default dict with ``None`` and crash every downstream consumer
    that expects a mapping (#58277).
    """
    result = base.copy()
    for key, value in override.items():
        over_dict = isinstance(result.get(key), dict)
        if over_dict and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        elif not (over_dict and value is None):
            result[key] = value
    return result


_ENV_REF_RE = re.compile(r"\${([^}]+)}")


def _env_ref_lookup(name: str) -> Optional[str]:
    """Resolve the env var behind a ``${VAR}`` / ``${env:VAR}`` ref — plain ``os.environ`` outside
    a profile secret scope (legacy behavior for the default profile).

    Inside a scope (a multiplexed gateway turn, a secondary profile's config load, a cron job) the read goes
    through ``agent.secret_scope.get_secret`` so the ref resolves against *that* profile's ``.env``: under
    multiplexing a miss is a miss, never another profile's ``os.environ`` value (#84079 — every profile
    "had" the default profile's ``${MATRIX_ACCESS_TOKEN}`` and fanned out). Same policy as
    ``gateway.config._getenv`` and ``get_env_value``.
    """
    try:
        from agent.secret_scope import current_secret_scope, get_secret as _get_secret
    except Exception:
        return os.environ.get(name)
    if current_secret_scope() is None:
        return os.environ.get(name)
    return _get_secret(name)


def _env_expand_match(m: re.Match) -> str:
    """Expand one ``${VAR}`` (legacy bare name) or ``${env:VAR}`` (Cursor-style SecretRef).
    Other SecretRef sources (``file:``, ``bitwarden:``, ``vault:``...) are NOT resolved here:
    external backends inject their values into the environment at startup (the ``secrets:``
    block), so a config ref only ever needs the env shape. Unresolved refs stay verbatim so
    callers can detect them."""
    raw = m.group(0)
    inner = m.group(1).strip()
    name = _env_ref_var_name(inner)
    if name is None:
        if not inner.startswith("env:") and _is_non_env_secret_ref(inner):
            logger.warning(
                "Config ref %r uses source %r which is not resolvable in "
                "config.yaml — external secret sources inject env vars at "
                "startup, so reference the variable as ${env:NAME} instead",
                raw, inner.split(":", 1)[0])
        return raw  # non-env source, or empty ``${env:}``
    val = _env_ref_lookup(name)
    if val is not None:
        return val
    if inner.startswith("env:"):
        logger.warning(
            "Config ref %r: %s is not set (check ~/.eidolon/.env); "
            "keeping the literal placeholder", raw, name)
    return raw


def _is_non_env_secret_ref(ref: str) -> bool:
    """True for a SecretRef body with a non-``env`` source (``bitwarden:FOO``, ``vault:...``)."""
    return ":" in ref and re.match(r"^[a-z][a-z0-9_-]*:", ref) is not None


def _env_ref_var_name(ref: str) -> Optional[str]:
    """Env-var name a ``${...}`` body reads, or None for a non-env source / empty ``env:``."""
    ref = ref.strip()
    if ref.startswith("env:"):
        return ref[len("env:"):].strip() or None
    if _is_non_env_secret_ref(ref):
        return None
    return ref


def _expand_env_vars(obj):
    """Recursively expand ``${VAR}`` / ``${env:VAR}`` in string values (keys/non-strings untouched)."""
    if isinstance(obj, str):
        return _ENV_REF_RE.sub(_env_expand_match, obj)
    if isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_vars(item) for item in obj]
    return obj


def _normalize_root_model_keys(config: Dict[str, Any]) -> Dict[str, Any]:
    """Canonicalize the ``model`` section at the single load/save chokepoint.
    Root-level ``provider``/``base_url``/``context_length`` (older layouts) are moved under
    ``model`` only when the corresponding ``model.*`` key is empty — never overriding. ``api_base``
    (the OpenAI-SDK/LiteLLM name users reach for) is an alias for ``base_url``; the runtime reads
    only ``model.base_url``. A dict-valued ``default``/``model``/``name`` is flattened so no reader
    sees a nested dict, and the id is canonicalized to ``default``.

    Also aliases ``api_base`` → ``base_url`` (issue #8919). ``api_base`` is the intuitive name OpenAI-SDK /
    LiteLLM users reach for, and ``eidolon config set`` blindly accepts any dotted key — so
    ``model.api_base`` got written, confirmed, and then silently ignored by the runtime resolver (which
    reads only ``model.base_url``), causing requests to fall back to OpenRouter. We migrate the alias to the
    canonical key (fallback-only — never override an explicit ``base_url``) and drop the alias so it can't
    confuse later loads.
    Finally, canonicalizes the model-id key to ``model.default`` (issue #34500). The runtime resolver and
    ~14 other readers select the chat model via ``model.default``; ``model.model`` was already aliased
    inline at some sites but ``model.name`` was not, so a custom-provider config like ``model: {name: <id>,
    provider: <custom>}`` resolved to an empty model and the API request went out with ``model=`` (HTTP 400
    from OpenAI-compatible backends) — while display paths (``eidolon status``/``dump``) read ``name`` and
    *showed* the model, making the failure silent. Normalizing here (the single load/save chokepoint) means
    every reader, present and future, sees a populated ``default`` and the stale alias is migrated out of
    config.yaml on the next save. Precedence: ``default`` > ``model`` > ``name`` (never overrides an
    explicit ``default``, so existing configs are unaffected).
    """
    model_in = config.get("model")
    needs_model_work = isinstance(model_in, dict) and (
        model_in.get("api_base")
        or model_in.get("model") or model_in.get("name")
        or any(isinstance(model_in.get(k), dict) for k in ("default", "model", "name")))
    has_root = any(config.get(k) for k in ("provider", "base_url", "context_length", "api_base"))
    if not has_root and not needs_model_work:
        return config

    config = dict(config)
    model = config.get("model")
    model = dict(model) if isinstance(model, dict) else {"default": model} if model else {}
    config["model"] = model

    # Flatten ``{provider: <p>, model: <m>}``. The nested provider wins over the merged default
    # ``"auto"`` (which runtime resolution treats as authoritative) but never over a configured one.
    for _key in ("default", "model", "name"):
        _val = model.get(_key)
        if isinstance(_val, dict):
            _nested_model = _val.get("model") or _val.get("default")
            _nested_provider = str(_val.get("provider") or "").strip()
            model[_key] = str(_nested_model or "").strip()
            if _nested_provider:
                _outer_provider = str(model.get("provider") or "").strip()
                if not _outer_provider or _outer_provider == "auto":
                    model["provider"] = _nested_provider

    for key in ("provider", "base_url", "context_length"):
        root_val = config.get(key)
        if root_val and not model.get(key):
            model[key] = root_val
        config.pop(key, None)

    for alias_val in (config.get("api_base"), model.get("api_base")):
        if alias_val and not model.get("base_url"):
            model["base_url"] = alias_val
    config.pop("api_base", None)
    model.pop("api_base", None)

    # ``model``/``name`` are last-resort aliases (in that order), then dropped.
    alias = model.get("model") or model.get("name")
    if not model.get("default") and alias:
        model["default"] = alias
    if model.get("default"):
        model.pop("model", None)
        model.pop("name", None)

    return config


def _merge_managed_overlay(expanded: Dict[str, Any]) -> Tuple[Dict[str, Any], Any]:
    """Apply the managed-scope overlay; returns ``(merged, managed_config_or_falsy)``.
    Managed wins at the leaf and is applied AFTER user expansion so a user ``${VAR}`` cannot shadow
    a managed literal: managed values expand only against the process environment. This
    deliberately inverts the usual env-over-config precedence for the keys the managed layer pins
    (docs/design/managed-scope.md §4.1)."""
    managed_config = managed_scope.load_managed_config()
    if not managed_config:
        return expanded, managed_config
    # Same canonicalization as the user config BEFORE merging (parity with
    # managed_scope.apply_managed_overlay) so the merged result never exposes a nested dict.
    managed_normalized = _normalize_root_model_keys(managed_config)
    if isinstance(managed_normalized.get("model"), str):
        managed_normalized = dict(managed_normalized)
        managed_normalized["model"] = {"default": managed_normalized["model"]}
    return _deep_merge(expanded, _expand_env_vars(managed_normalized)), managed_config


def _parse_env_value(raw_value: str) -> str:
    """Parse the small .env value subset Eidolon writes itself (bare, 'single', or "double" with
    ``\\"`` / ``\\\\`` escapes)."""
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        quoted = value[1:-1]
        parsed: list[str] = []
        i = 0
        while i < len(quoted):
            escaped = quoted[i] == "\\" and quoted[i + 1:i + 2] in ('"', "\\")
            parsed.append(quoted[i + 1] if escaped else quoted[i])
            i += 2 if escaped else 1
        return "".join(parsed)
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1]
    return value
