import asyncio
import concurrent.futures
import inspect
import multiprocessing
import traceback

from starlette.concurrency import run_in_threadpool

__all__ = ["BackgroundQueue"]


class BackgroundQueue:
    """A queue for running tasks in background threads.

    Uses a ``ThreadPoolExecutor`` sized to the number of CPUs. Access it
    via ``api.background``.

    Usage::

        # As a decorator — fire and forget
        @api.background.task
        def send_email(to, subject):
            ...

        send_email("user@example.com", "Hello")

        # Direct submission
        future = api.background.run(send_email, "user@example.com", "Hello")

        # As a callable (supports async functions)
        await api.background(send_email, "user@example.com", "Hello")

    """

    def __init__(self, n=None):
        """Create a new background queue.

        :param n: Number of worker threads. Defaults to CPU count.
        """
        if n is None:
            n = multiprocessing.cpu_count()

        self.n = n
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=n)
        self.results = []

    def run(self, f, *args, **kwargs):
        """Submit a function to run in a background thread.

        :param f: The function to run.
        :returns: A ``concurrent.futures.Future`` for the result.
        """
        f = self.pool.submit(f, *args, **kwargs)
        self.results.append(f)
        return f

    def task(self, f):
        """Decorator that wraps a function to run in the background thread pool.

        The decorated function returns a ``Future`` instead of blocking.
        Exceptions are printed to stderr via traceback.

        :param f: The function to wrap.
        """

        def on_future_done(fs):
            try:
                fs.result()
            except Exception:
                traceback.print_exc()

        def do_task(*args, **kwargs):
            result = self.run(f, *args, **kwargs)
            result.add_done_callback(on_future_done)
            return result

        return do_task

    async def __call__(self, func, *args, **kwargs) -> None:
        if inspect.iscoroutinefunction(func):
            return await asyncio.create_task(func(*args, **kwargs))
        return await run_in_threadpool(func, *args, **kwargs)
