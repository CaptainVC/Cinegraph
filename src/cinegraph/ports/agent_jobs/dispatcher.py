from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from threading import BoundedSemaphore, Lock
from typing import Protocol

from cinegraph.common.error_messages import AgentJobErrorMessages
from cinegraph.config import DEFAULT_AGENT_JOB_CONFIGURATION, AgentJobConfiguration


class AgentJobDispatcher(Protocol):
    def dispatch(self, callback: Callable[[], None]) -> bool: ...
    def close(self) -> None: ...


class InlineAgentJobDispatcher:
    def __init__(self) -> None:
        self.closed = False

    def dispatch(self, callback: Callable[[], None]) -> bool:
        if self.closed:
            return False
        callback()
        return True

    def close(self) -> None:
        self.closed = True


class BoundedThreadPoolAgentJobDispatcher:
    """Bounded admission wrapper around an executor; rejected jobs remain queued."""

    def __init__(
        self,
        max_workers: int | None = None,
        max_pending: int | None = None,
        executor: Executor | None = None,
        configuration: AgentJobConfiguration = DEFAULT_AGENT_JOB_CONFIGURATION,
    ) -> None:
        max_workers = configuration.worker_limit if max_workers is None else max_workers
        max_pending = configuration.pending_limit if max_pending is None else max_pending
        if max_workers < 1 or max_pending < 1:
            raise ValueError(AgentJobErrorMessages.DISPATCHER_LIMITS)
        self._executor = executor or ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="cinegraph-agent"
        )
        self._owns_executor = executor is None
        self._slots = BoundedSemaphore(max_pending)
        self._lock = Lock()
        self._closed = False

    def dispatch(self, callback: Callable[[], None]) -> bool:
        with self._lock:
            if self._closed or not self._slots.acquire(blocking=False):
                return False
            try:
                self._executor.submit(self._run, callback)
            except Exception:
                self._slots.release()
                return False
            return True

    def _run(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        finally:
            self._slots.release()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self._owns_executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
