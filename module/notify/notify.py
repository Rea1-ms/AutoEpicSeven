import onepush.core
from onepush import get_notifier
from onepush.core import Provider
from onepush.exceptions import OnePushException
from onepush.providers.custom import Custom
from requests import Response
from yaml import YAMLError

from module.logger import logger
from module.notify.config import has_system_provider, parse_notification_channels
from module.runtime_events import runtime_events

onepush.core.log = logger


def is_system_provider(_config: str) -> bool:
    """Keep compatibility with callers checking the legacy local channel."""
    return has_system_provider(_config)


def handle_notify(_config: str, *, system_notification: bool | None = None, **kwargs) -> bool:
    """Try each enabled destination; success means at least one accepted it."""
    try:
        channels = parse_notification_channels(_config)
    except (TypeError, ValueError, YAMLError):
        logger.error('Fail to load remote notification config, skip remote sending')
        channels = []

    # The explicit switch is authoritative, including False. Only callers that
    # have not adopted it may infer an opt-in from the legacy system provider.
    # Send locally before network I/O: a slow or broken remote channel must not
    # prevent a ready desktop from receiving the same event.
    legacy = next((channel for channel in channels if isinstance(channel, dict)
                   and str(channel.get('provider', '')).strip().lower() in {'system', '系统'}), {})
    if system_notification is None:
        system_notification = bool(legacy)
    accepted = False
    if system_notification:
        try:
            message = {**legacy, **kwargs}
            accepted = bool(runtime_events.notify(
                title=message.get('title', 'AutoEpicSeven'),
                content=message.get('content', ''),
            ))
            logger.info('System notification queued for an active interface' if accepted
                        else 'No active system notification interface; skip sending')
        except Exception:  # noqa: BLE001 - A local failure must not cancel remote delivery.
            logger.error('System notification queue unavailable; remote sending will continue')

    for index, channel in enumerate(channels, 1):
        if not isinstance(channel, dict):
            logger.error(f'Invalid remote notification entry {index}, skip this channel')
            continue
        provider = channel.get('provider')
        if provider is None or str(provider).strip().lower() in {'system', '系统'}:
            continue
        # Do not short-circuit after an accepted destination. Every configured
        # channel must be tried even when another destination already succeeded.
        remote_accepted = _send_remote_notification(dict(channel), **kwargs)
        logger.info(f'Remote notification channel {index}: {"accepted" if remote_accepted else "failed"}')
        accepted = remote_accepted or accepted
    return accepted


def _send_remote_notification(config: dict, **kwargs) -> bool:
    try:
        provider_name: str = config.pop("provider", None)
        if provider_name is None:
            logger.info("No provider specified, skip sending")
            return False
        notifier: Provider = get_notifier(provider_name)
        required: list[str] = notifier.params["required"]
        config.update(kwargs)

        # pre check
        for key in required:
            if key not in config:
                logger.warning(
                    f"Notifier {notifier.name} require param '{key}' but not provided"
                )

        if isinstance(notifier, Custom):
            if "method" not in config or config["method"] == "post":
                config["datatype"] = "json"
            if not isinstance(config.get("data"), dict):
                config["data"] = {}
            if "title" in kwargs:
                config["data"]["title"] = kwargs["title"]
            if "content" in kwargs:
                config["data"]["content"] = kwargs["content"]

        if provider_name.lower() == "gocqhttp":
            access_token = config.get("access_token")
            if access_token:
                config["token"] = access_token

        provider_key = provider_name.lower()
        resp = notifier.notify(**config)
        # onepush may log a transport error, retry internally, then swallow the
        # final failure and return None from its request helper. Treat None as a
        # definitive delivery failure here so we never report a false success.
        if resp is None:
            logger.error("Push notify failed: no response returned")
            return False
        if not isinstance(resp, Response):
            logger.error(
                f"Push notify failed: unexpected response type {type(resp).__name__}"
            )
            return False
        if resp.status_code != 200:
            logger.warning("Push notify failed!")
            logger.warning(f"HTTP Code:{resp.status_code}")
            return False
        if provider_key == "gocqhttp":
            return_data: dict = resp.json()
            if return_data["status"] == "failed":
                logger.warning("Push notify failed!")
                logger.warning(
                    f"Return message:{return_data['wording']}")
                return False
    except OnePushException:
        logger.error("Push notify failed")
        return False
    except Exception as e:  # noqa: BLE001 - Provider failures must not cancel other destinations.
        # Exception messages can contain destination URLs and credentials.
        logger.error(f'Push notify failed: {type(e).__name__}')
        return False

    logger.info("Push notify success")
    return True
