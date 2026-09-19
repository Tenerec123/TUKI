"""Per-conversation inference cost accumulator.

A ``ContextVar`` holding a MUTABLE holder object sums OpenRouter inference
costs during one agentic turn. ``accumulate()`` mutates the shared holder in
place, so every context copy — including ``asyncio.TaskGroup`` child tasks
that run tool calls (e.g. the nested WebSearch searcher agent) — sees the
accumulated value. A bare ``Decimal`` ContextVar would silently lose the
nested searcher's cost, because child tasks copy the context (and therefore
the immutable Decimal reference) at task creation.

ARMED-WINDOW CONTRACT
---------------------
``accumulate()`` only has effect inside a ``reset()``…``consume()`` window
in the owning context; callers outside a window are ignored.

- ``reset()`` ARMS the accumulator: it installs a fresh zeroed, ARMED holder
  in the current context and marks the turn as started.
- ``consume()`` reads the current total, replaces the holder with a zeroed
  DISARMED one, and returns the total — the window closes after exactly one
  read.
- The shared default holder (created ONCE at import) is DISARMED, so every
  context that never calls ``reset()`` — the voice-agent pipeline, any
  future non-chat caller — may call ``accumulate()`` freely and the call is
  silently ignored. The default can never accumulate leaked spend.

Holder replacement happens only in the owning context (``reset()`` /
``consume()``), which is safe: ``asyncio.TaskGroup`` children copy the ARMED
holder reference at task creation and mutate it in place, so the parent
reads their contributions at ``consume()``.
"""
from contextvars import ContextVar
from decimal import Decimal


class _TurnCost:
    """Mutable holder so accumulate() is visible across TaskGroup children.

    ``armed`` marks an active reset()…consume() window; accumulate() mutates
    only holders whose owning context opened a window.
    """

    __slots__ = ("total", "armed")

    def __init__(self, armed: bool = False) -> None:
        self.total = Decimal("0")
        self.armed = armed


_cost: ContextVar[_TurnCost] = ContextVar("conversation_cost", default=_TurnCost())


def reset() -> None:
    """Arm the turn accumulator (called once at wrapper start).

    Installs a zeroed, ARMED holder in the current context. TaskGroup
    children created afterwards copy this holder reference, so their
    accumulate() calls land in the same object the parent will consume.
    """
    _cost.set(_TurnCost(armed=True))


def accumulate(cost: Decimal | float | None) -> None:
    """Add one inference's cost to the shared turn total.

    MUTATES the holder in place — never replaces it — so the value is
    visible across ``asyncio.TaskGroup`` child contexts. NO-OP unless the
    current context's holder is armed (inside a ``reset()``…``consume()``
    window); absent or falsy costs (free/BYOK models without ``usage.cost``)
    contribute 0.
    """
    if not cost:
        return
    holder = _cost.get()
    if holder.armed:
        holder.total += Decimal(str(cost))


def consume() -> Decimal:
    """Read the turn total, DISARM the accumulator, and return the total.

    Called exactly once per turn in the owning context. If the context never
    armed the accumulator (e.g. a wrapper exception before ``reset()``), the
    holder is the unarmed default: the read is 0 and no spend is recorded.
    """
    t = _cost.get().total
    _cost.set(_TurnCost())  # zeroed, disarmed — window closed
    return t