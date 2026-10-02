"""Evaluate whether a responder produced any user-visible output."""

from collections.abc import Mapping, Sequence


VISIBLE_CHANNELS = (
    "commentary",
    "progress",
    "status",
    "final",
    "other_visible",
)


def _messages(value, channel):
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        messages = []
        for item in value:
            if not isinstance(item, str):
                raise TypeError(f"{channel} entries must be strings")
            if item:
                messages.append(item)
        return messages
    raise TypeError(f"{channel} must be a string or sequence of strings")


def evaluate_visible_transcript(transcript):
    """Return a fail-closed silence decision for the complete visible transcript."""
    if not isinstance(transcript, Mapping):
        raise TypeError("transcript must be a mapping")

    visible_messages = []
    nonempty_channels = []
    for channel in VISIBLE_CHANNELS:
        messages = _messages(transcript.get(channel, ""), channel)
        if messages:
            nonempty_channels.append(channel)
            visible_messages.extend(
                {"channel": channel, "content": message} for message in messages
            )

    return {
        "silent": not visible_messages,
        "visible_message_count": len(visible_messages),
        "nonempty_channels": nonempty_channels,
        "visible_messages": visible_messages,
    }
