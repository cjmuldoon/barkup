"""Simple scheduler for nightly summary."""

import logging
import threading
from datetime import datetime, time, timedelta

logger = logging.getLogger(__name__)


class DailyScheduler:
    def __init__(self, target_time: time, callback: callable):
        """
        Args:
            target_time: Time of day to run (e.g., time(20, 0) for 8pm).
            callback: Function to call at the scheduled time.
        """
        self._target_time = target_time
        self._callback = callback
        self._timer = None
        self._running = False

    def start(self):
        self._running = True
        self._schedule_next()
        logger.info("Daily scheduler started for %s", self._target_time)

    def _schedule_next(self):
        if not self._running:
            return

        now = datetime.now()
        target = now.replace(
            hour=self._target_time.hour,
            minute=self._target_time.minute,
            second=0,
            microsecond=0,
        )

        # If target time has passed today, schedule for tomorrow
        if target <= now:
            target += timedelta(days=1)

        delay = (target - now).total_seconds()
        logger.info("Next summary in %.0f seconds (at %s)", delay, target)

        self._timer = threading.Timer(delay, self._run)
        self._timer.daemon = True
        self._timer.start()

    def _run(self):
        try:
            self._callback()
        except Exception:
            logger.exception("Scheduled callback failed")
        finally:
            self._schedule_next()

    def stop(self):
        self._running = False
        if self._timer:
            self._timer.cancel()
