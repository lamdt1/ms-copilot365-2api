import time
import uuid
import logging
from typing import Dict, Tuple

logger = logging.getLogger(__name__)


class SessionManager:
    """
    Manages state for Substrate's SessionId and ConversationId mappings.
    If client requests state persistence via X-M365-Session-Id, we retrieve
    their context. Otherwise, stateless mode generates fresh IDs.
    """
    SESSION_TTL = 24 * 3600  # 24 hours — sessions idle longer than this are evicted

    def __init__(self):
        # Format: { persistent_id: (session_id, conversation_id, msg_count, created_at, last_used) }
        self._sessions: Dict[str, dict] = {}

    def _evict_stale(self):
        """Evict sessions not used for SESSION_TTL seconds. Called lazily on access."""
        now = time.time()
        cutoff = now - self.SESSION_TTL
        stale = [k for k, v in self._sessions.items() if v.get("last_used", 0) < cutoff]
        for k in stale:
            del self._sessions[k]
        if stale:
            logger.debug("SessionManager: evicted %d stale sessions", len(stale))

    def get_or_create_context(self, persistent_id: str = None) -> Tuple[str, str, bool, int]:
        """
        Returns (session_id, conversation_id, is_start_of_session, msg_count)
        """
        # Periodic eviction
        if len(self._sessions) > 100:
            self._evict_stale()

        if not persistent_id:
            # Stateless mode: new UUIDs every time
            sid = str(uuid.uuid4())
            cid = str(uuid.uuid4())
            return sid, cid, True, 0

        # Persistent mode
        ctx = self._sessions.get(persistent_id)
        if ctx:
            ctx["last_used"] = time.time()
            sid = ctx["session_id"]
            cid = ctx["conversation_id"]
            msg_count = ctx["msg_count"]
            is_start = False

            # Check limit
            if msg_count >= 600:
                logger.warning("Session %s reached 600 msgs. Re-rolling ConversationId.", persistent_id)
                cid = str(uuid.uuid4())
                ctx["conversation_id"] = cid
                ctx["msg_count"] = 0
                is_start = True
                msg_count = 0

            return sid, cid, is_start, msg_count

        # Create new persistent context
        sid = str(uuid.uuid4())
        cid = str(uuid.uuid4())
        now = time.time()
        self._sessions[persistent_id] = {
            "session_id": sid,
            "conversation_id": cid,
            "msg_count": 0,
            "created_at": now,
            "last_used": now
        }
        return sid, cid, True, 0

    def increment_msg_count(self, persistent_id: str, count: int = 1):
        if persistent_id and persistent_id in self._sessions:
            self._sessions[persistent_id]["msg_count"] += count
            self._sessions[persistent_id]["last_used"] = time.time()

    def get_session(self, persistent_id: str) -> dict:
        self._evict_stale()
        ctx = self._sessions.get(persistent_id)
        if ctx:
            ctx["last_used"] = time.time()
        return ctx

    def delete_session(self, persistent_id: str) -> bool:
        if persistent_id in self._sessions:
            del self._sessions[persistent_id]
            return True
        return False

    def clear_all(self):
        self._sessions.clear()


session_manager = SessionManager()
