"""A small typed user-directory API.

Run it:

    responder run examples/user.py

Try it with:

    curl http://127.0.0.1:5042/users
    curl -H "Content-Type: application/json" \
         -d '{"username": "ada", "full_name": "Ada Lovelace"}' \
         http://127.0.0.1:5042/users
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

import responder


class UserIn(BaseModel):
    username: str = Field(min_length=1, examples=["ada"])
    full_name: str = Field(min_length=1, examples=["Ada Lovelace"])


class UserOut(UserIn):
    id: int
    active: bool


USER_EXAMPLE = {
    "id": 1,
    "username": "ada",
    "full_name": "Ada Lovelace",
    "active": True,
}


@dataclass
class UserStore:
    users: dict[int, UserOut] = field(default_factory=dict)
    next_id: int = 1

    @classmethod
    def seeded(cls) -> UserStore:
        store = cls()
        store.create(UserIn(username="ada", full_name="Ada Lovelace"))
        store.create(UserIn(username="grace", full_name="Grace Hopper"))
        return store

    def list(self) -> list[UserOut]:
        return [
            user.model_copy(deep=True)
            for user in sorted(self.users.values(), key=lambda item: item.id)
        ]

    def get(self, user_id: int) -> UserOut | None:
        user = self.users.get(user_id)
        return None if user is None else user.model_copy(deep=True)

    def create(self, user: UserIn) -> UserOut:
        user_id = self.next_id
        self.next_id += 1
        record = UserOut(id=user_id, active=True, **user.model_dump())
        self.users[user_id] = record
        return record.model_copy(deep=True)


def create_api(*, store: UserStore | None = None) -> responder.API:
    store = store or UserStore.seeded()
    api = responder.API(
        title="User Directory",
        version="1.0",
        openapi="3.1.0",
        docs_route="/docs",
        sessions=False,
    )

    @api.get("/", include_in_schema=False)
    def index(req, resp):
        resp.media = {"name": "User Directory", "users": "/users", "docs": "/docs"}

    @api.get(
        "/users",
        operation_id="list_users",
        tags=["users"],
        summary="List users",
        response_model=list[UserOut],
        examples={"seed": {"value": [USER_EXAMPLE]}},
    )
    def list_users(req, resp):
        resp.media = store.list()

    @api.post(
        "/users",
        operation_id="create_user",
        tags=["users"],
        summary="Create a user",
        response_model=UserOut,
        responses={201: "User created"},
        response_examples={201: {"created": {"value": USER_EXAMPLE}}},
    )
    def create_user(req, resp, *, user: UserIn):
        created = store.create(user)
        resp.created(created, location=f"/users/{created.id}")

    @api.get(
        "/users/{user_id:int}",
        operation_id="get_user",
        tags=["users"],
        summary="Fetch a user",
        response_model=UserOut,
        responses={404: "User not found"},
        examples={"ada": {"value": USER_EXAMPLE}},
    )
    def get_user(req, resp, *, user_id: int):
        user = store.get(user_id)
        if user is None:
            resp.problem(404, f"User {user_id} does not exist.", user_id=user_id)
            return
        resp.media = user

    return api


api = create_api()


if __name__ == "__main__":
    api.run()
