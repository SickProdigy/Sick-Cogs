import logging

from redbot.core import Config

from .models import migrate_feed_data

CONFIG_IDENTIFIER = 2761331001
RSS_VERSION = "2.3.0"

def create_config(cog) -> Config:
    """Register the stable RSS configuration schema."""
    config = Config.get_conf(cog, CONFIG_IDENTIFIER, force_registration=True)
    config.register_channel(feeds={})
    config.register_global(
        use_published=["www.youtube.com"],
        private_feed_hosts=[],
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
