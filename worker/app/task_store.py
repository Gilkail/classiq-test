"""Terminal task-state writer for the worker (``task:{id}`` HASH).

Phase 3 fills this in with ``set_completed()`` and ``set_error()`` helpers that
write the terminal state, stamp ``updated_at``, and refresh the TTL
(REFERENCE §5.1).
"""

# TODO(Phase 3): implement set_completed(), set_error().
