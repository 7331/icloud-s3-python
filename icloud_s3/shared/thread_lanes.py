import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
import contextvars
import functools

from icloud_s3.shared.errors import SlowDownError


class ThreadLane:
    def __init__(self, name: str, *, threads: int, queue_timeout_seconds: float) -> None:
        self.name = name
        self.queue_timeout_seconds = queue_timeout_seconds
        self.executor = ThreadPoolExecutor(max_workers=threads, thread_name_prefix=f"icloud-s3-{name}")
        self.permits = asyncio.Semaphore(threads)

    async def run[**P, R](self, function: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R:
        try:
            async with asyncio.timeout(self.queue_timeout_seconds):
                await self.permits.acquire()
        except TimeoutError as error:
            message = f"{self.name} is busy; try again"
            raise SlowDownError(message) from error
        context = contextvars.copy_context()
        call = functools.partial(context.run, functools.partial(function, *args, **kwargs))
        work = asyncio.get_running_loop().run_in_executor(self.executor, call)
        work.add_done_callback(self.release_permit)
        return await asyncio.shield(work)

    def release_permit[R](self, work: asyncio.Future[R]) -> None:
        if not work.cancelled():
            work.exception()
        self.permits.release()

    def shut_down(self) -> None:
        self.executor.shutdown(wait=True)
