"""When each timeframe is scanned.

GitHub Actions cron fires at minute 2, 17, 32 and 47 (about two minutes after
15m candle closes). Higher timeframes ride the same clock and only run on the
slot that matches their own close, plus a staleness backstop if a run was missed.
"""
import datetime as dt

TFS = ["15m", "30m", "1h", "4h", "12h", "1d", "3d", "1w", "1M"]
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SLOTS = (2, 17, 32, 47)
# Backstop if the matching slot was missed entirely (Actions delays, skipped crons).
MAX_AGE = {
    "15m": 20 * 60,
    "30m": 40 * 60,
    "1h": 80 * 60,
    "4h": 4 * 3600 + 20 * 60,
    "12h": 13 * 3600,
    "1d": 26 * 3600,
    "3d": 3 * 86400 + 6 * 3600,
    "1w": 8 * 86400,
    "1M": 32 * 86400,
}


def _utc(now):
    if now is None:
        now = dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    return now.astimezone(dt.timezone.utc)


def floor_slot(now=None):
    """Most recent cron minute (2, 17, 32, 47) at or before `now`."""
    now = _utc(now).replace(second=0, microsecond=0)
    prev = [s for s in SLOTS if s <= now.minute]
    if prev:
        return now.replace(minute=prev[-1])
    return (now - dt.timedelta(hours=1)).replace(minute=SLOTS[-1])


def matches(tf, slot):
    slot = _utc(slot)
    minute, hour, weekday, day = slot.minute, slot.hour, slot.weekday(), slot.day
    if tf == "15m":
        return minute in SLOTS
    if tf == "30m":
        return minute in (2, 32)
    if tf == "1h":
        return minute == 2
    if tf == "4h":
        return minute == 2 and hour % 4 == 0
    if tf == "12h":
        return minute == 2 and hour in (0, 12)
    if tf == "1d":
        return minute == 2 and hour == 0
    if tf == "3d":
        return minute == 2 and hour == 0 and (slot.date().toordinal() % 3 == 0)
    if tf == "1w":
        return minute == 2 and hour == 0 and weekday == 0  # Monday, after the weekly close
    if tf == "1M":
        return minute == 2 and hour == 0 and day == 1
    raise KeyError(tf)


def latest_slot(tf, now=None):
    """Latest cron slot at or before `now` that scans `tf`."""
    t = floor_slot(now)
    limit = MAX_AGE[tf] + 86400
    start = t.timestamp()
    while start - t.timestamp() <= limit:
        if matches(tf, t):
            return t
        t -= dt.timedelta(minutes=15)
    return None


def next_slot(tf, now=None):
    """Next cron slot strictly after `now` that scans `tf`."""
    now = _utc(now)
    t = floor_slot(now) + dt.timedelta(minutes=15)
    for _ in range(4000):
        if matches(tf, t) and t > now:
            return t
        t += dt.timedelta(minutes=15)
    raise RuntimeError(f"no upcoming slot for {tf}")


def due(tf, last_ts, now=None):
    """True when this timeframe should be scanned now."""
    if not last_ts:
        return True
    now = _utc(now)
    slot = latest_slot(tf, now)
    if slot and last_ts < slot.timestamp() - 30:
        return True
    if now.timestamp() - last_ts >= MAX_AGE[tf]:
        return True
    return False


def select(mode, meta_tfs, now=None):
    """mode: 'due', 'all', or a comma-separated timeframe list."""
    mode = (mode or "due").strip()
    if mode == "all":
        return list(TFS)
    if mode != "due":
        out = []
        for part in mode.split(","):
            tf = part.strip()
            if tf not in TFS:
                raise SystemExit(f"Unknown timeframe {tf!r}. Use one of: {', '.join(TFS)}, due, all")
            out.append(tf)
        return out
    chosen = []
    for tf in TFS:
        info = (meta_tfs or {}).get(tf) or {}
        if due(tf, info.get("generated_ts") or 0, now):
            chosen.append(tf)
    return chosen


def fmt_ist(when):
    if isinstance(when, (int, float)):
        when = dt.datetime.fromtimestamp(when, IST)
    else:
        when = _utc(when).astimezone(IST)
    return when.strftime("%d %b, %I:%M %p IST")
