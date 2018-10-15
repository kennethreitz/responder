import multiprocessing
import concurrent.futures


class BackgroundQueue:
    def __init__(self, n=None):
        if n is None:
            n = multiprocessing.cpu_count()

        self.n = n
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=n)
        self.results = []

    def run(self, f, *args, **kwargs):
        self.pool._max_workers = self.n
        self.pool._adjust_thread_count()

        f = self.pool.submit(f, *args, **kwargs)
        self.results.append(f)
        return f

    def task(self, f):
        def do_task(*args, **kwargs):
            result = self.run(f, *args, **kwargs)
            return result

        return do_task
