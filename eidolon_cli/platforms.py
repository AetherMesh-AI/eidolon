"""Shared platform registry for Eidolon Agent."""

from collections import OrderedDict
from typing import NamedTuple


class PlatformInfo(NamedTuple):
    """Metadata for a single platform entry."""
    label: str
    default_toolset: str


# Ordered so that TUI menus are deterministic.
PLATFORMS: OrderedDict[str, PlatformInfo] = OrderedDict([
    ("cli",            PlatformInfo(label="🖥️  CLI",            default_toolset="eidolon-cli")),
    ("telegram",       PlatformInfo(label="📱 Telegram",        default_toolset="eidolon-telegram")),
    ("discord",        PlatformInfo(label="💬 Discord",         default_toolset="eidolon-discord")),
    ("slack",          PlatformInfo(label="💼 Slack",           default_toolset="eidolon-slack")),
    ("whatsapp",       PlatformInfo(label="📱 WhatsApp",        default_toolset="eidolon-whatsapp")),
    ("whatsapp_cloud", PlatformInfo(label="📱 WhatsApp Business (Cloud)", default_toolset="eidolon-whatsapp")),
    ("signal",         PlatformInfo(label="📡 Signal",          default_toolset="eidolon-signal")),
    ("bluebubbles",    PlatformInfo(label="💙 BlueBubbles",     default_toolset="eidolon-bluebubbles")),
    ("email",          PlatformInfo(label="📧 Email",           default_toolset="eidolon-email")),
    ("homeassistant",  PlatformInfo(label="🏠 Home Assistant",  default_toolset="eidolon-homeassistant")),
    ("mattermost",     PlatformInfo(label="💬 Mattermost",      default_toolset="eidolon-mattermost")),
    ("matrix",         PlatformInfo(label="💬 Matrix",          default_toolset="eidolon-matrix")),
    ("dingtalk",       PlatformInfo(label="💬 DingTalk",        default_toolset="eidolon-dingtalk")),
    ("feishu",         PlatformInfo(label="🪽 Feishu",          default_toolset="eidolon-feishu")),
    ("wecom",          PlatformInfo(label="💬 WeCom",           default_toolset="eidolon-wecom")),
    ("wecom_callback", PlatformInfo(label="💬 WeCom Callback",  default_toolset="eidolon-wecom-callback")),
    ("weixin",         PlatformInfo(label="💬 Weixin",          default_toolset="eidolon-weixin")),
    ("qqbot",          PlatformInfo(label="💬 QQBot",           default_toolset="eidolon-qqbot")),
    ("yuanbao",        PlatformInfo(label="🤖 Yuanbao",         default_toolset="eidolon-yuanbao")),
    ("webhook",        PlatformInfo(label="🔗 Webhook",         default_toolset="eidolon-webhook")),
    ("api_server",     PlatformInfo(label="🌐 API Server",      default_toolset="eidolon-api-server")),
    ("cron",           PlatformInfo(label="⏰ Cron",            default_toolset="eidolon-cron")),
])


def _plugin_label(entry) -> str:
    return f"{entry.emoji}  {entry.label}" if entry.emoji else entry.label


def platform_label(key: str, default: str = "") -> str:
    """Return the display label for a platform key (builtin, then plugin registry), or *default*."""
    info = PLATFORMS.get(key)
    if info is not None:
        return info.label
    try:
        from gateway.platform_registry import platform_registry
        entry = platform_registry.get(key)
        if entry:
            return _plugin_label(entry)
    except Exception:
        pass
    return default


def get_all_platforms() -> "OrderedDict[str, PlatformInfo]":
    """PLATFORMS plus plugin-registered platforms (appended after builtins) — use for menus."""
    merged = OrderedDict(PLATFORMS)
    try:
        from gateway.platform_registry import platform_registry
        for entry in platform_registry.plugin_entries():
            if entry.name not in merged:
                merged[entry.name] = PlatformInfo(_plugin_label(entry), f"eidolon-{entry.name}")
    except Exception:
        pass
    return merged
