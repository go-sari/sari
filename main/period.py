from datetime import datetime

import pytz
import tzlocal

_local_tz = pytz.timezone(tzlocal.get_localzone().zone)


class Validator:
    now = datetime.now(pytz.utc)

    def is_valid(self, not_valid_before: datetime, not_valid_after: datetime) -> bool:
        if not_valid_before and self.now < _to_utc(not_valid_before):
            return False
        if not_valid_after and self.now > _to_utc(not_valid_after):
            return False
        return True


class TrackingValidator(Validator):
    """
    A datetime period validator that reports the next instant where at least one of the validations will have a
    different result.
    """
    now: datetime = datetime.now(pytz.utc)
    next_transition: datetime = None

    def is_valid(self, not_valid_before: datetime, not_valid_after: datetime) -> bool:

        for dt in [_to_utc(dt) for dt in [not_valid_before, not_valid_after] if dt]:
            if dt and dt > self.now and (not self.next_transition or dt < self.next_transition):
                self.next_transition = dt
        return super().is_valid(not_valid_before, not_valid_after)


def _to_utc(dt_: datetime) -> datetime:
    if dt_.tzinfo:
        return dt_.astimezone(pytz.utc)
    return _local_tz.localize(dt_)
