import logging

from redbot.core import Config

from .models import migrate_feed_data

LEGACY_CONFIG_IDENTIFIER = 2761331001
CONFIG_IDENTIFIER = 842719563204817695
RSS_VERSION = "3.1.1"
RSS_SCHEMA_VERSION = 1

def create_config(cog) -> Config:
    """Register the stable RSS configuration schema."""
    config = Config.get_conf(cog, CONFIG_IDENTIFIER, force_registration=True)
    config.register_channel(feeds={})
    config.register_global(
        use_published=["www.youtube.com"],
        private_feed_hosts=[],
        schema_version=0,
        migration_status={"state": "not_started", "imported_feeds": 0, "review": []},
    )
    return config

async def migrate_stored_feeds(config: Config, log: logging.Logger) -> int:
    """Add current feed defaults without replacing existing settings."""
    config_data = await config.all_channels()
    migrated_count = 0
    for channel_id, channel_data in config_data.items():
        feeds = channel_data.get("feeds", {})
        changed = False
        for feed_name, feed_data in list(feeds.items()):
            migrated, feed_changed = migrate_feed_data(feed_data)
            if feed_changed:
                feeds[feed_name] = migrated
                changed = True
                migrated_count += 1
        if changed:
            await config.channel_from_id(int(channel_id)).feeds.set(feeds)
    if migrated_count:
        log.info(
            "Migrated %s RSS feed configuration(s) to version %s",
            migrated_count,
            RSS_VERSION,
        )
    return migrated_count


async def migrate_legacy_rss(config: Config, log: logging.Logger) -> dict:
    """Copy legacy RSS feeds once into the Sick-Cogs namespace."""
    if await config.schema_version() >= RSS_SCHEMA_VERSION:
        return await config.migration_status()
    if await config.all_channels():
        status = {"state": "needs_owner_review", "imported_feeds": 0, "review": ["Target namespace already contains channel data."]}
        await config.migration_status.set(status)
        return status
    legacy = Config.get_conf(None, LEGACY_CONFIG_IDENTIFIER, cog_name="RSS")
    await config.migration_status.set({"state": "migrating", "imported_feeds": 0, "review": []})
    review, imported = [], 0
    legacy_global = await legacy.all()
    for key in ("use_published", "private_feed_hosts"):
        if key in legacy_global and isinstance(legacy_global[key], list):
            await getattr(config, key).set(legacy_global[key])
    for channel_id, channel_data in (await legacy.all_channels()).items():
        feeds = channel_data.get("feeds", {}) if isinstance(channel_data, dict) else {}
        if not isinstance(feeds, dict):
            review.append(f"channel {channel_id}: malformed feeds mapping")
            continue
        copied = {}
        for name, data in feeds.items():
            if not isinstance(name, str) or not isinstance(data, dict) or not data.get("url"):
                review.append(f"channel {channel_id}: skipped malformed feed {name!r}")
                continue
            copied[name], _ = migrate_feed_data(data)
            imported += 1
        if copied:
            await config.channel_from_id(int(channel_id)).feeds.set(copied)
    status = {"state": "completed", "imported_feeds": imported, "review": review, "legacy_identifier": LEGACY_CONFIG_IDENTIFIER}
    await config.schema_version.set(RSS_SCHEMA_VERSION)
    await config.migration_status.set(status)
    log.info("Imported %s legacy RSS feed(s); %s review warning(s).", imported, len(review))
    return status
