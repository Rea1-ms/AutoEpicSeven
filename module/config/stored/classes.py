from datetime import datetime, timedelta
from functools import cached_property as functools_cached_property

from module.base.decorator import cached_property
from module.config.deep import deep_get
from module.config.utils import DEFAULT_TIME, get_server_last_monday_update, get_server_last_update


def now():
    return datetime.now().replace(microsecond=0)


def iter_attribute(cls):
    """
    Args:
        cls: Class or object

    Yields:
        str, obj: Attribute name, attribute value
    """
    for attr in dir(cls):
        if attr.startswith('_'):
            continue
        value = getattr(cls, attr)
        if type(value).__name__ in ['function', 'property']:
            continue
        yield attr, value


class StoredBase:
    time = DEFAULT_TIME

    def __init__(self, key):
        self._key = key
        self._config = None

    @cached_property
    def _name(self):
        return self._key.split('.')[-1]

    def _bind(self, config):
        """
        Args:
            config (AzurLaneConfig):
        """
        self._config = config

    @functools_cached_property
    def _stored(self):
        assert self._config is not None, 'StoredBase._bind() must be called before getting stored data'
        from module.logger import logger

        out = {}
        stored = deep_get(self._config.data, keys=self._key, default={})
        for attr, default in self._attrs.items():
            value = stored.get(attr, default)
            if attr == 'time':
                if not isinstance(value, datetime):
                    try:
                        value = datetime.fromisoformat(value)
                    except ValueError:
                        logger.warning(f'{self._name} has invalid attr: {attr}={value}, use default={default}')
                        value = default
            else:
                if not isinstance(value, type(default)):
                    logger.warning(f'{self._name} has invalid attr: {attr}={value}, use default={default}')
                    value = default

            out[attr] = value
        return out

    @cached_property
    def _attrs(self) -> dict:
        """
        All attributes defined
        """
        attrs = {
            # time is the first one
            'time': DEFAULT_TIME
        }
        for attr, value in iter_attribute(self.__class__):
            if attr.islower():
                attrs[attr] = value
        return attrs

    def __setattr__(self, key, value):
        if key in self._attrs:
            stored = self._stored
            stored['time'] = now()
            stored[key] = value
            self._config.modified[self._key] = stored
            if self._config.auto_update:
                self._config.update()
        else:
            super().__setattr__(key, value)

    def __getattribute__(self, item):
        if not item.startswith('_') and item in self._attrs:
            return self._stored[item]
        else:
            return super().__getattribute__(item)

    def is_expired(self) -> bool:
        return False

    def show(self):
        """
        Log self
        """
        from module.logger import logger
        logger.attr(self._name, self._stored)


class StoredExpiredAt0400(StoredBase):
    def is_expired(self):
        from module.logger import logger
        self.show()
        expired = self.time < get_server_last_update('04:00')
        logger.attr(f'{self._name} expired', expired)
        return expired


class StoredExpiredAtMonday0400(StoredBase):
    def is_expired(self):
        from module.logger import logger
        self.show()
        expired = self.time < get_server_last_monday_update('04:00')
        logger.attr(f'{self._name} expired', expired)
        return expired


class StoredInt(StoredBase):
    value = 0

    def clear(self):
        self.value = 0


class StoredCounter(StoredBase):
    value = 0
    total = 0

    FIXED_TOTAL = 0

    def set(self, value, total=0):
        if self.FIXED_TOTAL:
            total = self.FIXED_TOTAL
        with self._config.multi_set():
            self.value = value
            self.total = total

    def clear(self):
        self.value = 0

    def to_counter(self) -> str:
        return f'{self.value}/{self.total}'

    def is_full(self) -> bool:
        return self.value >= self.total

    def get_remain(self) -> int:
        return self.total - self.value

    def add(self, value=1):
        self.value += value

    @cached_property
    def _attrs(self) -> dict:
        attrs = super()._attrs
        if self.FIXED_TOTAL:
            attrs['total'] = self.FIXED_TOTAL
        return attrs

    @functools_cached_property
    def _stored(self):
        stored = super()._stored
        if self.FIXED_TOTAL:
            stored['total'] = self.FIXED_TOTAL
        return stored


class StoredNaturalRecoverCounter(StoredCounter):
    """
    Counter that recovers 1 point every RECOVER_SECONDS by natural regeneration.

    Regeneration model, shared by E7 stamina and arena flags:
    - Natural recovery only happens while `value < total`. Once the counter
      reaches `total`, regeneration stops; time above the cap is wasted.
    - `value` may legally sit above `total` (stamina potions, event flags,
      observed 19521/336 and 26/5). Such overflow amounts never regenerate,
      so prediction must return the raw value untouched instead of clamping
      it down to `total`.
    - `time` is stamped by StoredBase on every write, so it is the moment of
      the last trustworthy OCR/estimate and is the anchor for prediction.
    """

    # Seconds to recover 1 point. Subclasses must override.
    RECOVER_SECONDS = 0

    def predict_current(self) -> int:
        """
        Predict the current value from the last stored record.

        Returns:
            int: Predicted current value. Natural recovery is capped at
                `total`; values already at or above `total` are returned
                unchanged because they no longer regenerate.
        """
        value = self.value
        total = self.total
        # Overflowed counters no longer regenerate.
        if total <= 0 or value >= total:
            return value
        # Invalid record from the future, trust the raw value.
        record = self.time
        current = now()
        if record >= current:
            return value
        if self.RECOVER_SECONDS <= 0:
            return value
        elapsed = (current - record).total_seconds()
        value += int(elapsed // self.RECOVER_SECONDS)
        return min(value, total)

    def predict_reach_time(self, target: int) -> datetime:
        """
        Predict the wall-clock time when the counter reaches `target`.

        Args:
            target: Desired value. Callers must pre-clamp it to `total`,
                because natural recovery can never exceed the cap.

        Returns:
            datetime: `now()` when the predicted value already satisfies the
                target, otherwise the predicted future moment. Partial
                progress toward the next point (elapsed % RECOVER_SECONDS)
                is credited so repeated re-scheduling does not drift late.
        """
        current = now()
        predicted = self.predict_current()
        if predicted >= target:
            return current
        if self.RECOVER_SECONDS <= 0:
            return current
        deficit = target - predicted
        wait = deficit * self.RECOVER_SECONDS
        # Credit the partial progress toward the next recovery tick.
        record = self.time
        if record < current:
            wait -= (current - record).total_seconds() % self.RECOVER_SECONDS
            wait = max(wait, 0)
        return (current + timedelta(seconds=wait)).replace(microsecond=0)


class StoredStamina(StoredNaturalRecoverCounter):
    # E7 stamina recovers 1 point every 4 minutes.
    RECOVER_SECONDS = 4 * 60


class StoredArenaFlag(StoredNaturalRecoverCounter):
    # E7 arena flags recover 1 flag every hour.
    # No FIXED_TOTAL on purpose: `total <= 0` is the only signal that the
    # counter has never been OCR'd. A FIXED_TOTAL would fabricate `0/5` for
    # fresh configs and make arena combat skip itself forever.
    RECOVER_SECONDS = 60 * 60


class StoredArenaRank(StoredCounter):
    FIXED_TOTAL = 38


class StoredDailyActivity(StoredCounter):
    FIXED_TOTAL = 100


class StoredShadowCommission(StoredCounter):
    FIXED_TOTAL = 30


class StoredTeamBattleStatus(StoredBase):
    value = ''
