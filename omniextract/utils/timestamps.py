from PyQt6.QtCore import QTime


def format_timestamp(milliseconds, separator=":"):
    """Format millisecond integer into HH:MM:SS.mmm string."""
    milliseconds = max(0, int(milliseconds))
    hours = milliseconds // 3_600_000
    milliseconds %= 3_600_000
    minutes = milliseconds // 60_000
    milliseconds %= 60_000
    seconds = milliseconds // 1_000
    millis = milliseconds % 1_000
    return f"{hours:02d}{separator}{minutes:02d}{separator}{seconds:02d}.{millis:03d}"


def qtime_to_ms(qtime):
    """Convert QTime object to total milliseconds."""
    return (
        qtime.hour() * 3_600_000
        + qtime.minute() * 60_000
        + qtime.second() * 1_000
        + qtime.msec()
    )


def ms_to_qtime(milliseconds):
    """Convert millisecond integer to a QTime object bounded within 24 hours."""
    milliseconds = max(0, int(milliseconds))
    milliseconds = min(
        milliseconds,
        23 * 3_600_000 + 59 * 60_000 + 59 * 1_000 + 999
    )
    hours = milliseconds // 3_600_000
    milliseconds %= 3_600_000
    minutes = milliseconds // 60_000
    milliseconds %= 60_000
    seconds = milliseconds // 1_000
    msec = milliseconds % 1_000
    return QTime(hours, minutes, seconds, msec)


def duration_text(milliseconds):
    """Convenience alias for format_timestamp."""
    return format_timestamp(milliseconds)


def frame_to_ms(frame, fps):
    """Convert frame number to millisecond timestamp given an FPS value."""
    return round((frame / fps) * 1000) if fps else 0
