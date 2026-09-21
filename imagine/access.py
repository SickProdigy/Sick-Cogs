from typing import Iterable

from .models import AccessDecision


def evaluate_access(*, globally_enabled: bool, guild_allowlisted: bool,
                    guild_enabled: bool, user_id: int, role_ids: Iterable[int],
                    allowed_users: Iterable[int], allowed_roles: Iterable[int],
                    is_owner: bool = False) -> AccessDecision:
    """Apply Imagine's default-deny policy without Discord dependencies."""
    if not globally_enabled:
        return AccessDecision(False, "Image generation is disabled globally.")
    if not guild_allowlisted:
        return AccessDecision(False, "This server is not allowed to use image generation.")
    if not guild_enabled:
        return AccessDecision(False, "Image generation is disabled in this server.")
    users = {int(value) for value in allowed_users}
    roles = {int(value) for value in allowed_roles}
    if is_owner:
        return AccessDecision(True)
    if not users and not roles:
        return AccessDecision(False, "No users or roles have been granted image access.")
    if int(user_id) in users or roles.intersection(int(value) for value in role_ids):
        return AccessDecision(True)
    return AccessDecision(False, "You have not been granted access to image generation.")
