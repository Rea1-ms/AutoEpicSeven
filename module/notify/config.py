"""Parse notification settings without loading network providers."""

import yaml


def parse_notification_channels(value: str) -> list:
    """Keep legacy YAML document merging and accept an explicit channel list."""
    if not isinstance(value, str):
        raise TypeError('Notification configuration must be YAML text')
    documents = [item for item in yaml.safe_load_all(value) if item is not None]
    if len(documents) == 1 and isinstance(documents[0], list):
        return documents[0]
    merged = {}
    for item in documents:
        if not isinstance(item, dict):
            raise TypeError('Notification configuration must be a mapping or channel list')
        merged.update(item)
    return [merged]


def has_system_provider(value: str) -> bool:
    """Recognize a legacy local opt-in without sending or importing onepush."""
    try:
        return any(
            isinstance(channel, dict)
            and str(channel.get('provider', '')).strip().lower() in {'system', '系统'}
            for channel in parse_notification_channels(value)
        )
    except (TypeError, ValueError, yaml.YAMLError):
        return False
