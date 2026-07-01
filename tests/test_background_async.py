"""BackgroundQueue supports async functions (they used to be silently dropped)."""

import asyncio
import functools
import threading

import pytest

from responder.background import BackgroundQueue


@pytest.fixture
def queue():
    q = BackgroundQueue(n=2)
    yield q
    q.shutdown()


def test_run_async_function(queue):
    """An async def submitted via .run() actually executes and returns a result."""
    ran = []

    async def job(x):
        await asyncio.sleep(0)
        ran.append(x)
        return x * 2

    future = queue.run(job, 21)
    assert future.result(timeout=5) == 42
    assert ran == [21]


def test_run_async_function_kwargs(queue):
    async def job(*, name):
        return f"hello {name}"

    future = queue.run(job, name="sam")
    assert future.result(timeout=5) == "hello sam"


def test_run_async_partial(queue):
    """functools.partial wrapping an async def is detected and awaited."""

    async def job(x, y):
        return x + y

    future = queue.run(functools.partial(job, 40), 2)
    assert future.result(timeout=5) == 42


def test_task_decorator_async_function(queue):
    """@background.task on an async def runs the coroutine to completion."""
    done = threading.Event()

    @queue.task
    async def job():
        await asyncio.sleep(0)
        done.set()
        return "ok"

    future = job()
    assert future.result(timeout=5) == "ok"
    assert done.is_set()


def test_run_async_function_exception_propagates(queue):
    async def job():
        raise ValueError("boom")

    future = queue.run(job)
    with pytest.raises(ValueError, match="boom"):
        future.result(timeout=5)


def test_run_sync_function_unchanged(queue):
    """Sync submission behavior is identical to before."""

    def job(x):
        return x + 1

    future = queue.run(job, 1)
    assert future.result(timeout=5) == 2


def test_task_decorator_sync_function_unchanged(queue):
    @queue.task
    def job(x):
        return x * 3

    assert job(3).result(timeout=5) == 9


def test_worker_thread_name_prefix(queue):
    def job():
        return threading.current_thread().name

    name = queue.run(job).result(timeout=5)
    assert name.startswith("responder-background")


def test_await_call_form_still_awaits_async(queue):
    """The awaitable __call__ form still awaits async callables directly."""

    async def job(x):
        return x * 2

    async def main():
        return await queue(job, 5)

    assert asyncio.run(main()) == 10


def test_api_background_async_task(api):
    """End-to-end: an async fire-and-forget task via api.background runs."""
    done = threading.Event()

    @api.route("/")
    def route(req, resp):
        @api.background.task
        async def job():
            await asyncio.sleep(0)
            done.set()

        job()
        resp.media = {"status": "accepted"}

    r = api.requests.get("/")
    assert r.status_code == 200
    assert done.wait(timeout=5)
