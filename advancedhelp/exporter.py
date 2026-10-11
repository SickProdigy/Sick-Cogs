"""Sanitized, deterministic command inventory export helpers."""

from datetime import datetime, timezone

from .models import classify_audience, classify_category, iter_command_chain

SCHEMA_VERSION = 1


def _permission_names(requires, attribute):
    permissions = getattr(requires, attribute, None)
    if permissions is None or not getattr(permissions, "value", 0):
        return []
    return sorted(name for name, enabled in permissions if enabled)


def declared_access(command):
    """Return inherited declared access without evaluating a user or guild context."""
    levels = set()
    user_permissions = set()
    bot_permissions = set()
    for current in iter_command_chain(command):
        requires = getattr(current, "requires", None)
        if requires is None:
            continue
        level = getattr(getattr(requires, "privilege_level", None), "name", "NONE")
        if level and level != "NONE":
            levels.add(level)
        user_permissions.update(_permission_names(requires, "user_perms"))
        bot_permissions.update(_permission_names(requires, "bot_perms"))

    if "BOT_OWNER" in levels:
        label = "Bot maintainer"
    elif "GUILD_OWNER" in levels:
        label = (
            "Server owner or required Discord permission"
            if user_permissions
            else "Server owner"
        )
    elif "ADMIN" in levels:
        label = (
            "Administrator or required Discord permission"
            if user_permissions
            else "Administrator"
        )
    elif "MOD" in levels:
        label = (
            "Moderator or required Discord permission"
            if user_permissions
            else "Moderator"
        )
    elif user_permissions:
        label = "Required Discord permission"
    else:
        label = "Member"

    return {
        "label": label,
        "privilege_levels": sorted(levels),
        "user_permissions": sorted(user_permissions),
        "bot_permissions": sorted(bot_permissions),
    }


def command_record(command, ctx, audience_overrides, category_overrides):
    """Build one public command metadata record."""
    cog = getattr(command, "cog", None)
    description = ""
    formatter = getattr(command, "format_shortdoc_for_context", None)
    if formatter is not None:
        description = formatter(ctx) or ""
    if not description:
        description = getattr(command, "short_doc", "") or ""

    return {
        "path": command.qualified_name,
        "aliases": sorted(set(getattr(command, "aliases", []) or [])),
        "module": getattr(cog, "qualified_name", None) or "Core",
        "category": classify_category(command, category_overrides),
        "audience": classify_audience(command, audience_overrides),
        "description": description,
        "signature": getattr(command, "signature", "") or "",
        "hidden": bool(getattr(command, "hidden", False)),
        "enabled": bool(getattr(command, "enabled", True)),
        "declared_access": declared_access(command),
    }


def build_inventory(
    bot,
    ctx,
    global_settings,
    *,
    prefix,
    red_version,
    advancedhelp_version,
    generated_at=None,
):
    """Build a sanitized command inventory from the active Red command registry."""
    audience_overrides = global_settings.get("audience_overrides", {})
    category_overrides = global_settings.get("category_overrides", {})
    commands = sorted(
        {
            command.qualified_name: command
            for command in bot.walk_commands()
        }.values(),
        key=lambda command: command.qualified_name,
    )
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    loaded_cogs = sorted(
        getattr(cog, "qualified_name", name)
        for name, cog in getattr(bot, "cogs", {}).items()
    )
    loaded_extensions = sorted(getattr(bot, "extensions", {}).keys())
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "red_version": str(red_version),
        "advancedhelp_version": str(advancedhelp_version),
        "prefix": prefix,
        "loaded_cogs": loaded_cogs,
        "loaded_extensions": loaded_extensions,
        "command_count": len(commands),
        "commands": [
            command_record(
                command, ctx, audience_overrides, category_overrides
            )
            for command in commands
        ],
        "notice": (
            "This file contains declared command metadata only. Live Red permissions, "
            "disabled state, parent checks, role hierarchy, and server rules remain authoritative."
        ),
    }
