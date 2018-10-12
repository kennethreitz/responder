import multiprocessing
import concurrent.futures


class BackgroundQueue:
    def __init__(self, n=None):
        if n is None:
            n = multiprocessing.cpu_count()

        self.n = n
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=n)
        self.results = []
        self.callbacks = []

    def run(self, f, *args, **kwargs):
        self.pool._max_workers = self.n
        self.pool._adjust_thread_count()

        f = self.pool.submit(f, *args, **kwargs)
        self.results.append(f)

    def task(self, f):
        def do_task(*args, **kwargs):
            result = self.run(f, *args, **kwargs)

            for cb in self.callbacks:
                result.add_done_callback(cb)

            return result

        return do_task

    def callback(self, f):
        self.callbacks.append(f)

        def register_callback():
            f()

        return register_callback
