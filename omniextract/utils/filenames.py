import os
from .timestamps import format_timestamp


def render_filename(template, frame_number, timestamp_ms, extension):
    """Render a filename given a template string, frame number, timestamp in ms, and extension."""
    timestamp_file = format_timestamp(timestamp_ms, "-")
    template = template.strip() if template else ""
    if not template:
        template = "frame_{timestamp}"

    values = {
        "frame": str(frame_number),
        "timestamp": timestamp_file,
        "milliseconds": str(timestamp_ms),
        "hour": f"{timestamp_ms // 3_600_000:02d}",
        "minute": f"{(timestamp_ms // 60_000) % 60:02d}",
        "second": f"{(timestamp_ms // 1_000) % 60:02d}",
    }

    try:
        name = template.format(**values)
    except (KeyError, ValueError):
        name = f"frame_{timestamp_file}"

    name = "".join(
        c if c not in '<>:"/\\|?*' else "_"
        for c in name
    ).strip()

    if not name:
        name = f"frame_{timestamp_file}"

    if not name.lower().endswith(extension.lower()):
        name += extension

    return name


def unique_path(directory, filename, used):
    """Generate a non-colliding filename in the given directory and used set."""
    stem, extension = os.path.splitext(filename)
    candidate = filename
    counter = 1

    while candidate in used or os.path.exists(os.path.join(directory, candidate)):
        candidate = f"{stem}_{counter:03d}{extension}"
        counter += 1

    used.add(candidate)
    return os.path.join(directory, candidate)
