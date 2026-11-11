"""Completion-based automatic harvesting, driven by the existing UI timer."""
import math
import time


def interval_minutes(value):
    minutes = float(value)
    if not math.isfinite(minutes) or not 1 <= minutes <= 1440:
        raise ValueError('自动收菜间隔需在 1 到 1440 分钟之间。')
    return minutes


class AutoHarvest:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.enabled = False
        self.minutes = 30
        self.completed_at = None

    def start(self, minutes):
        self.minutes = interval_minutes(minutes)
        self.enabled = True
        self.completed_at = None

    def finish(self):
        if self.enabled:
            self.completed_at = self.clock()

    def change_interval(self, minutes):
        self.minutes = interval_minutes(minutes)

    def remaining(self):
        if not self.enabled or self.completed_at is None:
            return None
        return max(0, math.ceil(self.completed_at + self.minutes * 60 - self.clock()))

    def take_due(self):
        if self.remaining() != 0:
            return False
        self.completed_at = None
        return True

    def cancel(self):
        self.enabled = False
        self.completed_at = None
