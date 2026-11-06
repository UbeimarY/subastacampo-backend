"""Business calendar.

Servers run in UTC, but dates such as "harvested today" belong to the farmers'
time zone. Instants (timestamps) stay in UTC; only calendar dates use this.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo


class BusinessCalendar:
    def __init__(self, timezone: str) -> None:
        self._zone = ZoneInfo(timezone)

    def today(self) -> date:
        return datetime.now(self._zone).date()

    