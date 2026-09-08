"""Local WFRL Bridge protocol primitives shared by backend adapters."""

from .messages import (
    FrameDecoder,
    ProtocolError,
    SessionSequenceGuard,
    channel_is_stale,
    data_age_seconds,
    encode_message,
    normalize_rotor_speed,
    validate_message,
)

__all__ = [
    "FrameDecoder",
    "ProtocolError",
    "SessionSequenceGuard",
    "channel_is_stale",
    "data_age_seconds",
    "encode_message",
    "normalize_rotor_speed",
    "validate_message",
]
