import asyncio
import json
from dataclasses import dataclass, field


@dataclass
class _StreamState:
    """Internal state for one conversation's stream."""
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    active: bool = False


class StreamManager:
    """Manages streaming state per conversation.

    Each conversation gets its own queue and active flag,
    allowing multiple conversations to stream simultaneously.
    Only one stream per conversation at a time.

    Events are serialized to NDJSON (one JSON object per line,
    each line terminated by \\n) so the frontend can buffer and
    split on newlines regardless of chunk boundaries.
    """

    def __init__(self):
        self._streams: dict[int, _StreamState] = {}

    def start(self, conv_id: int) -> bool:
        """Register a new stream for this conversation.

        Returns False if the conversation already has an active stream.
        Stale entries (finished streams) are silently replaced.
        """
        existing = self._streams.get(conv_id)
        if existing and existing.active:
            return False
        self._streams[conv_id] = _StreamState(active=True)
        return True

    def push(self, conv_id: int, event: dict):
        """Serialize an event to NDJSON and push it to the stream queue.

        Each event becomes one line (json.dumps + newline), so the frontend
        can split the byte stream on '\\n' and parse each line independently.
        """
        state = self._streams.get(conv_id)
        if state and state.active:
            state.queue.put_nowait(json.dumps(event) + "\n")

    def finish(self, conv_id: int):
        """Mark the conversation's stream as finished."""
        state = self._streams.get(conv_id)
        if state:
            state.active = False

    def is_active(self, conv_id: int) -> bool:
        """Check if a conversation is currently streaming."""
        state = self._streams.get(conv_id)
        return state is not None and state.active

    async def stream(self, conv_id: int):
        """Async generator that yields NDJSON lines for this conversation.

        1. Drains everything already queued as a single burst.
        2. Streams events as they arrive until the stream finishes.

        Each yielded chunk is a string of one or more complete NDJSON
        lines (each terminated by \\n), so the frontend can split on
        newlines without special handling for burst vs live events.
        """
        state = self._streams.get(conv_id)
        if not state:
            return

        # Step 1 — drain accumulated events as initial burst
        initial = []
        while not state.queue.empty():
            try:
                initial.append(state.queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if initial:
            yield "".join(initial)

        # Step 2 — stream forward
        while state.active or not state.queue.empty():
            try:
                token = await asyncio.wait_for(state.queue.get(), timeout=0.5)
                yield token
            except asyncio.TimeoutError:
                continue

    def cleanup(self, conv_id: int):
        """Remove a conversation's stream state entirely."""
        self._streams.pop(conv_id, None)


# Singleton — import this from ai.py
stream_manager = StreamManager()
