"""Provider-independent role policy; never treats an API failure as ineligibility.

Only a future verified External Sign-In adapter may supply entitlements. This is
not a parser for provider responses and personal forum tokens are not identity
proof. Callers must serialize configuration and reconciliation per guild.
"""

import re


class RoleSyncError(ValueError):
    pass


def entitlement_name(value):
    """Preserve case: provider permission identifiers are opaque and case-sensitive."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}", value):
        raise RoleSyncError("Use a permission name of 1–64 letters, digits, dots, colons, underscores or hyphens.")
    return value


def validate_role(guild, role, actor=None):
    if role is None or role.is_default() or role.managed or role.guild.id != guild.id:
        raise RoleSyncError("Choose a normal role from this server, not everyone or an integration role.")
    permissions = role.permissions
    if any(getattr(permissions, name, False) for name in (
        "administrator", "manage_guild", "manage_roles", "manage_channels",
        "manage_webhooks", "ban_members", "kick_members", "moderate_members",
    )):
        raise RoleSyncError("Membership mappings cannot grant administrative or moderation roles.")
    if guild.me is None or not guild.me.guild_permissions.manage_roles or role >= guild.me.top_role:
        raise RoleSyncError("I need Manage Roles and a role above the mapped role.")
    if actor is not None and actor.id != guild.owner_id and role >= actor.top_role:
        raise RoleSyncError("The mapped role must be below your highest role.")


def desired_roles(mappings, entitlements):
    # None means unavailable/unknown, NEVER an empty successful membership result.
    if not isinstance(entitlements, (set, frozenset)):
        raise RoleSyncError("Verified membership is unavailable; existing roles are unchanged.")
    for name in entitlements:
        entitlement_name(name)
    return {role_id for name, role_id in mappings.items() if name in entitlements}


async def reconcile_roles(member, mappings, entitlements, owned, save_owned):
    """Apply an authoritative snapshot and persist ownership before granting.

    `owned` includes pending grants from earlier interrupted attempts. Pre-existing
    unowned roles are never adopted or revoked. A save failure stops all further
    work. Discord failures leave intent tracked for retry. No bulk role replacement.
    The future caller must verify issuer/subject/Discord binding and snapshot
    freshness, and hold its guild lock across this call and mapping mutations.
    """
    desired = desired_roles(mappings, entitlements)
    tracked = set(owned)
    present = {role.id for role in member.roles}
    added, removed = [], []
    # Revoke first so loss of membership is not held up by an unrelated grant.
    for role_id in sorted(tracked - desired):
        role = member.guild.get_role(role_id)
        if role is not None and role_id in present:
            validate_role(member.guild, role)
            await member.remove_roles(role, reason="MyBB membership no longer grants this role")
            removed.append(role_id)
        tracked.remove(role_id)
        await save_owned(sorted(tracked))
    for role_id in sorted(desired - present):
        role = member.guild.get_role(role_id)
        validate_role(member.guild, role)
        tracked.add(role_id)
        await save_owned(sorted(tracked))
        await member.add_roles(role, reason="Verified MyBB membership")
        added.append(role_id)
    return added, removed
