import asyncio
import concurrent.futures
import multiprocessing
import traceback

from starlette.concurrency import run_in_threadpool


class BackgroundQueue:
    def __init__(self, n=None):
        if n is None:
            n = multiprocessing.cpu_count()

        self.n = n
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=n)
        self.results = []

    def run(self, f, *args, **kwargs):
        f = self.pool.submit(f, *args, **kwargs)
        self.results.append(f)
        return f

    def task(self, f):
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
        if asyncio.iscoroutinefunction(func):
            return await asyncio.create_task(func(*args, **kwargs))
        return await run_in_threadpool(func, *args, **kwargs)
