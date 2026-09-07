import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Hashable, Sequence

DEFAULT_INTERVAL = 300
MAX_BACKOFF = 3600
DEFAULT_CONCURRENCY = 5

@dataclass(frozen=True)
class FeedJob:
    key: Hashable
    channel: Any
    feed_name: str
    feed_data: dict

def retry_delay(
    consecutive_failures: int,
    base_interval: int = DEFAULT_INTERVAL,
    max_backoff: int = MAX_BACKOFF,
) -> int:
    """Return an exponential delay capped at one hour."""
    failures = max(0, int(consecutive_failures or 0))
    if failures == 0:
        return base_interval
    return min(max_backoff, base_interval * (2 ** (failures - 1)))

class FeedScheduler:
    """Poll due feeds concurrently while preventing overlapping checks."""

    def __init__(
        self,
        job_source: Callable[[], Awaitable[Sequence[FeedJob]]],
        check_job: Callable[[FeedJob], Awaitable[int]],
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        interval: int = DEFAULT_INTERVAL,
    ):
        self.job_source = job_source
        self.check_job = check_job
        self.interval = interval
        self._semaphore = asyncio.Semaphore(max(1, concurrency))
        self._next_due: dict[Hashable, float] = {}
        self._locks: dict[Hashable, asyncio.Lock] = {}

    async def run(self):
        loop = asyncio.get_running_loop()
        while True:
            jobs = await self.job_source()
            now = loop.time()
            active_keys = {job.key for job in jobs}
            self._next_due = {
                key: due for key, due in self._next_due.items() if key in active_keys
            }
            self._locks = {
                key: lock for key, lock in self._locks.items() if key in active_keys
            }

            due_jobs = [
                job for job in jobs if self._next_due.get(job.key, 0) <= now
            ]
            if due_jobs:
                await asyncio.gather(
                    *(self._run_job(job) for job in due_jobs),
                    return_exceptions=False,
                )

            if not jobs:
                await asyncio.sleep(30)
                continue

            now = loop.time()
            next_due = min(self._next_due.get(job.key, now) for job in jobs)
            await asyncio.sleep(max(1, min(30, next_due - now)))

    async def _run_job(self, job: FeedJob):
        lock = self._locks.setdefault(job.key, asyncio.Lock())
        if lock.locked():
            return

        async with lock:
            async with self._semaphore:
                failures = await self.check_job(job)
                self._next_due[job.key] = (
                    asyncio.get_running_loop().time()
                    + retry_delay(failures, self.interval)
                )
