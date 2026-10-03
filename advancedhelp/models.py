from dataclasses import dataclass, field
from typing import Dict, List

DEFAULT_AUDIENCE_ORDER = ("member", "staff", "server_owner", "bot_owner")
DEFAULT_AUDIENCE_LABELS = {
    "member": ("Member", "👤", "Everyday commands available to members."),
    "staff": ("Staff", "🛡️", "Moderation, management, and server setup."),
    "server_owner": ("Server Owner", "🏠", "Sensitive server-owner workflows."),
    "bot_owner": ("Bot Owner", "⚙️", "Global maintenance and host operations."),
}


@dataclass
class HelpCatalog:
    sections: Dict[str, Dict[str, List[object]]]
    audience_order: List[str] = field(default_factory=lambda: list(DEFAULT_AUDIENCE_ORDER))
    audience_labels: Dict[str, tuple] = field(
        default_factory=lambda: dict(DEFAULT_AUDIENCE_LABELS)
    )
    category_labels: Dict[str, dict] = field(default_factory=dict)

    @property
    def audiences(self):
        fixed = [key for key in self.audience_order if self.sections.get(key)]
        custom = sorted(
            key for key in self.sections if key not in self.audience_order and self.sections[key]
        )
        return fixed + custom

    def metadata(self, audience):
        return self.audience_labels.get(
            audience, (audience.title(), "🔖", "Available guidance.")
        )

    def category_metadata(self, category):
        data = self.category_labels.get(category, {})
        return (
            data.get("name", category),
            data.get("emoji"),
            data.get("description", ""),
        )

    def categories(self, audience):
        configured = {
            name: int(data.get("order", 1000))
            for name, data in self.category_labels.items()
        }
        return sorted(
            self.sections.get(audience, {}),
            key=lambda name: (configured.get(name, 1000), name.lower()),
        )

    def commands(self, audience, category):
        return self.sections.get(audience, {}).get(category, [])

    def add_role_group(
        self, key, display_name, categories, emoji="🔖", description=None
    ):
        section = {}
        wanted = set(categories)
        for audience in DEFAULT_AUDIENCE_ORDER:
            for category, commands in self.sections.get(audience, {}).items():
                if category in wanted:
                    section.setdefault(category, []).extend(commands)
        for commands in section.values():
            commands[:] = sorted(
                {command.qualified_name: command for command in commands}.values(),
                key=lambda item: item.qualified_name,
            )
        if section:
            section_key = "group:" + key
            self.sections[section_key] = section
            self.audience_order.append(section_key)
            self.audience_labels[section_key] = (
                display_name,
                emoji,
                description
                or "Role-specific guidance using commands you can already run.",
            )


def classify_audience(command, overrides):
    target = command.qualified_name.lower()
    if target in overrides:
        return overrides[target]
    level = getattr(getattr(command, "requires", None), "privilege_level", None)
    name = getattr(level, "name", "NONE")
    return {
        "BOT_OWNER": "bot_owner",
        "GUILD_OWNER": "server_owner",
        "ADMIN": "staff",
        "MOD": "staff",
    }.get(name, "member")


def classify_category(command, overrides):
    target = command.qualified_name.lower()
    if target in overrides:
        return overrides[target]
    cog = getattr(command, "cog", None)
    return getattr(cog, "qualified_name", None) or "Other"


def build_catalog(
    mapping,
    audience_overrides=None,
    category_overrides=None,
    audience_order=None,
    audience_appearance=None,
    category_appearance=None,
):
    audience_overrides = audience_overrides or {}
    category_overrides = category_overrides or {}
    sections = {key: {} for key in DEFAULT_AUDIENCE_ORDER}
    for _cog_name, commands in mapping:
        for command in commands.values():
            audience = classify_audience(command, audience_overrides)
            category = classify_category(command, category_overrides)
            sections[audience].setdefault(category, []).append(command)
    for categories in sections.values():
        for commands in categories.values():
            commands.sort(key=lambda item: item.qualified_name)
    labels = dict(DEFAULT_AUDIENCE_LABELS)
    for key, data in (audience_appearance or {}).items():
        default = labels.get(key, (key.title(), "🔖", "Available guidance."))
        labels[key] = (
            data.get("name", default[0]),
            data.get("emoji", default[1]),
            data.get("description", default[2]),
        )
    return HelpCatalog(
        sections,
        list(audience_order or DEFAULT_AUDIENCE_ORDER),
        labels,
        category_appearance or {},
    )
