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

        # Async functions work too — run via ``asyncio.run`` on the worker
        @api.background.task
        async def refresh_cache():
            ...

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
        self.pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=n, thread_name_prefix="responder-background"
        )
        self.results = []

    def run(self, f, *args, **kwargs):
        """Submit a function to run in a background thread.

        Async functions are supported: they are driven to completion on the
        worker thread via ``asyncio.run`` (previously they silently produced a
        never-awaited coroutine and the job never ran).

        :param f: The function to run.
        :returns: A ``concurrent.futures.Future`` for the result.
        """
        # ``inspect.iscoroutinefunction`` unwraps ``functools.partial`` itself.
        if inspect.iscoroutinefunction(f):

            def runner(*args, **kwargs):
                return asyncio.run(f(*args, **kwargs))

            future = self.pool.submit(runner, *args, **kwargs)
        else:
            future = self.pool.submit(f, *args, **kwargs)
        self.results.append(future)
        future.add_done_callback(self._discard)
        return future

    def _discard(self, future):
        # Drop completed futures so long-running apps don't accumulate them.
        try:
            self.results.remove(future)
        except ValueError:
            pass

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

    def shutdown(self, wait=True):
        """Stop accepting new tasks and, by default, drain in-flight ones.

        Called automatically at application shutdown so fire-and-forget tasks
        submitted via :meth:`run`/:meth:`task` are given a chance to finish
        rather than being abandoned when the process exits.

        :param wait: Block until running tasks complete (default ``True``).
        """
        self.pool.shutdown(wait=wait)

    async def __call__(self, func, *args, **kwargs):
        """Await ``func`` to completion, off the event loop if it is sync.

        Async callables are awaited directly; sync callables run in the thread
        pool so they don't block the loop. This form *awaits* the result — use
        :meth:`task` or :meth:`run` for true fire-and-forget scheduling.
        """
        if inspect.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        return await run_in_threadpool(func, *args, **kwargs)
