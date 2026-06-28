"""v5: async-native session & rate-limit backends + sliding TTL."""

import responder


class FakeAsyncSessionBackend:
    def __init__(self):
        self.store = {}
        self.touches = 0
        self.sets = 0

    async def aget(self, session_id):
        return self.store.get(session_id)

    async def aset(self, session_id, data, max_age):
        self.sets += 1
        self.store[session_id] = dict(data)

    async def atouch(self, session_id, max_age):
        self.touches += 1

    async def adelete(self, session_id):
        self.store.pop(session_id, None)


def test_async_session_backend_round_trip_and_touch():
    backend = FakeAsyncSessionBackend()
    api = responder.API(
        allowed_hosts=[";"], session_backend=backend, session_https_only=False
    )

    @api.route("/set", methods=["POST"])
    async def setv(req, resp):
        req.session["user"] = "kenneth"
        resp.text = "ok"

    @api.route("/get")
    async def getv(req, resp):
        resp.media = {"user": req.session.get("user")}

    client = api.requests
    client.post("/set")
    assert client.get("/get").json() == {"user": "kenneth"}
    # The unchanged read slid the TTL via atouch, not another aset.
    assert backend.touches >= 1
    assert backend.sets == 1


class FakeAsyncRateLimitBackend:
    def __init__(self):
        self.count = 0

    async def ahit(self, key, max_requests, period):
        self.count += 1
        if self.count > max_requests:
            return False, 0
        return True, max_requests - self.count


def test_async_ratelimit_backend():
    from responder.ext.ratelimit import RateLimiter

    api = responder.API(allowed_hosts=[";"], session_https_only=False)
    limiter = RateLimiter(requests=2, period=60, backend=FakeAsyncRateLimitBackend())
    limiter.install(api)

    @api.route("/")
    def view(req, resp):
        resp.text = "ok"

    client = api.requests
    assert client.get("/").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/").status_code == 429
