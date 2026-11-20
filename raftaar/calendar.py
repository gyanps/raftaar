"""Clock and calendar helpers. All model time is Indian Standard Time."""
from __future__ import annotations

import datetime as dt

import numpy as np

IST_OFFSET_S = 19800
BIN_S = 1800
N_BINS = 86400 // BIN_S
N_DAYTYPES = 3  # 0 = Monday-Friday, 1 = Saturday, 2 = Sunday or public holiday
DAYTYPE_NAMES = ("weekday", "saturday", "sunday_holiday")

# Gazetted holidays inside the data and the weeks after it (Delhi).
HOLIDAYS = {
    dt.date(2026, 10, 2),   # Gandhi Jayanti
    dt.date(2026, 10, 20),  # Dussehra
    dt.date(2026, 11, 8),   # Diwali (a Sunday)
    dt.date(2026, 11, 24),  # Guru Nanak Jayanti
    dt.date(2026, 12, 25),  # Christmas
}


def daytype_of_date(d: dt.date) -> int:
    if d in HOLIDAYS or d.weekday() == 6:
        return 2
    return 1 if d.weekday() == 5 else 0


def ist_date(epoch: float) -> dt.date:
    return (dt.datetime(1970, 1, 1) + dt.timedelta(seconds=float(epoch) + IST_OFFSET_S)).date()


def daytype(epoch) -> np.ndarray:
    """Day type of each epoch (vectorised)."""
    epoch = np.asarray(epoch, dtype=np.float64)
    days = np.floor((epoch + IST_OFFSET_S) / 86400).astype(np.int64)
    uniq, inv = np.unique(days, return_inverse=True)
    types = np.array([daytype_of_date(dt.date(1970, 1, 1) + dt.timedelta(days=int(u))) for u in uniq], dtype=np.int8)
    return types[inv].reshape(epoch.shape)


def second_of_day(epoch) -> np.ndarray:
    return np.mod(np.asarray(epoch, dtype=np.float64) + IST_OFFSET_S, 86400.0)


def epoch_ist(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> float:
    return (dt.datetime(year, month, day, hour, minute) - dt.datetime(1970, 1, 1)).total_seconds() - IST_OFFSET_S
