"""A practical Todo API example for Responder.

Run it:

    responder run examples/todo.py

Try it with:

    curl http://127.0.0.1:5042/todos
    curl -H "Authorization: Bearer demo-token" \
         -H "Content-Type: application/json" \
         -d '{"title": "Write the release notes", "tags": ["release"]}' \
         http://127.0.0.1:5042/todos
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

import responder
from responder.ext.auth import BearerAuth

TodoPriority = Literal["low", "normal", "high"]


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _clean_tags(tags: list[str]) -> list[str]:
    normalized: list[str] = []
    for tag in tags:
        cleaned = tag.strip().lower()
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return normalized


class TodoCreate(BaseModel):
    title: str = Field(min_length=1, examples=["Write the release notes"])
    notes: str = Field(default="", examples=["Link to the changelog."])
    due: date | None = Field(default=None, examples=["2026-07-01"])
    priority: TodoPriority = Field(default="normal", examples=["high"])
    tags: list[str] = Field(default_factory=list, examples=[["release", "docs"]])

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, tags: list[str]) -> list[str]:
        return _clean_tags(tags)


class TodoPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    notes: str | None = None
    due: date | None = None
    priority: TodoPriority | None = None
    tags: list[str] | None = None
    completed: bool | None = None

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, tags: list[str] | None) -> list[str] | None:
        return None if tags is None else _clean_tags(tags)


class TodoOut(TodoCreate):
    id: int
    completed: bool
    created_at: datetime
    completed_at: datetime | None
    owner: str


class User(BaseModel):
    name: str
    scopes: list[str]


TODO_EXAMPLE = {
    "id": 1,
    "title": "Write the release notes",
    "notes": "Link to the changelog.",
    "due": "2026-07-01",
    "priority": "high",
    "tags": ["release", "docs"],
    "completed": False,
    "created_at": "2026-06-30T12:00:00Z",
    "completed_at": None,
    "owner": "Ada",
}


@dataclass
class TodoStore:
    todos: dict[int, TodoOut] = field(default_factory=dict)
    next_id: int = 1

    @classmethod
    def seeded(cls) -> TodoStore:
        store = cls()
        store.create(
            TodoCreate(
                title="Sketch the release post",
                notes="Keep it short, useful, and link the changelog.",
                due=date(2026, 7, 1),
                priority="high",
                tags=["release", "writing"],
            ),
            owner="Ada",
        )
        done = store.create(
            TodoCreate(
                title="Review generated client ergonomics",
                notes="Check the golden contract app before shipping.",
                priority="normal",
                tags=["clientgen", "quality"],
            ),
            owner="Grace",
        )
        store.update(done.id, TodoPatch(completed=True))
        return store

    def all(
        self,
        *,
        completed: bool | None = None,
        tag: str | None = None,
    ) -> list[TodoOut]:
        rows = sorted(self.todos.values(), key=lambda item: item.id)
        if completed is not None:
            rows = [item for item in rows if item.completed is completed]
        if tag:
            wanted = tag.strip().lower()
            rows = [item for item in rows if wanted in item.tags]
        return [item.model_copy(deep=True) for item in rows]

    def get(self, todo_id: int) -> TodoOut | None:
        todo = self.todos.get(todo_id)
        return None if todo is None else todo.model_copy(deep=True)

    def create(self, todo: TodoCreate, *, owner: str) -> TodoOut:
        todo_id = self.next_id
        self.next_id += 1
        record = TodoOut(
            id=todo_id,
            completed=False,
            created_at=_now(),
            completed_at=None,
            owner=owner,
            **todo.model_dump(),
        )
        self.todos[todo_id] = record
        return record.model_copy(deep=True)

    def update(self, todo_id: int, patch: TodoPatch) -> TodoOut | None:
        todo = self.todos.get(todo_id)
        if todo is None:
            return None

        updates = patch.model_dump(exclude_unset=True)
        updates = {
            key: value
            for key, value in updates.items()
            if value is not None or key == "due"
        }

        if "completed" in updates:
            completed = updates["completed"]
            updates["completed_at"] = (
                todo.completed_at or _now()
            ) if completed else None

        updated = todo.model_copy(update=updates)
        self.todos[todo_id] = updated
        return updated.model_copy(deep=True)

    def complete(self, todo_id: int) -> TodoOut | None:
        return self.update(todo_id, TodoPatch(completed=True))

    def delete(self, todo_id: int) -> bool:
        return self.todos.pop(todo_id, None) is not None


def create_api(*, store: TodoStore | None = None) -> responder.API:
    store = store or TodoStore.seeded()
    users = {
        "demo-token": User(name="Ada", scopes=["todos:write"]),
        "ops-token": User(name="Grace", scopes=["todos:write"]),
    }

    bearer = BearerAuth(
        verify=lambda token: users.get(token),
        bearer_format="opaque",
        realm="todos",
    )

    api = responder.API(
        title="Todo API",
        version="1.0",
        openapi="3.1.0",
        docs_route="/docs",
        sessions=False,
        request_id=True,
    )

    writer = api.policy("writer", bearer.requires("todos:write"))

    @api.get("/", include_in_schema=False)
    def index(req, resp):
        resp.media = {"name": "Todo API", "todos": "/todos", "docs": "/docs"}

    @api.get(
        "/todos",
        operation_id="list_todos",
        tags=["todos"],
        summary="List todos",
        description="Return every todo, optionally filtered by completion or tag.",
        response_model=list[TodoOut],
        examples={"seed": {"summary": "Seeded todos", "value": [TODO_EXAMPLE]}},
    )
    def list_todos(
        req,
        resp,
        *,
        completed: bool | None = responder.Query(
            None,
            description="Only return active or completed todos.",
        ),
        tag: str | None = responder.Query(
            None,
            description="Only return todos with this tag.",
        ),
    ):
        resp.media = store.all(completed=completed, tag=tag)

    @api.post(
        "/todos",
        auth=writer,
        operation_id="create_todo",
        tags=["todos"],
        summary="Create a todo",
        response_model=TodoOut,
        responses={201: {"description": "Todo created"}},
        response_examples={201: {"created": {"value": TODO_EXAMPLE}}},
    )
    def create_todo(req, resp, *, user: User, todo: TodoCreate):
        created = store.create(todo, owner=user.name)
        resp.created(created, location=f"/todos/{created.id}")

    @api.get(
        "/todos/{todo_id:int}",
        operation_id="get_todo",
        tags=["todos"],
        summary="Fetch a todo",
        response_model=TodoOut,
        responses={404: "Todo not found"},
        examples={"release_notes": {"value": TODO_EXAMPLE}},
    )
    def get_todo(req, resp, *, todo_id: int):
        todo = store.get(todo_id)
        if todo is None:
            resp.problem(404, f"Todo {todo_id} does not exist.", todo_id=todo_id)
            return
        resp.media = todo

    @api.patch(
        "/todos/{todo_id:int}",
        auth=writer,
        operation_id="update_todo",
        tags=["todos"],
        summary="Update a todo",
        response_model=TodoOut,
        responses={400: "No changes supplied", 404: "Todo not found"},
    )
    def update_todo(req, resp, *, todo_id: int, user: User, patch: TodoPatch):
        if not patch.model_fields_set:
            resp.problem(400, "Send at least one field to update.", todo_id=todo_id)
            return
        todo = store.update(todo_id, patch)
        if todo is None:
            resp.problem(404, f"Todo {todo_id} does not exist.", todo_id=todo_id)
            return
        resp.media = todo

    @api.post(
        "/todos/{todo_id:int}/complete",
        auth=writer,
        operation_id="complete_todo",
        tags=["todos"],
        summary="Complete a todo",
        response_model=TodoOut,
        responses={404: "Todo not found"},
    )
    def complete_todo(req, resp, *, todo_id: int, user: User):
        todo = store.complete(todo_id)
        if todo is None:
            resp.problem(404, f"Todo {todo_id} does not exist.", todo_id=todo_id)
            return
        resp.media = todo

    @api.delete(
        "/todos/{todo_id:int}",
        auth=writer,
        operation_id="delete_todo",
        tags=["todos"],
        summary="Delete a todo",
        responses={204: "Todo deleted", 404: "Todo not found"},
    )
    def delete_todo(req, resp, *, todo_id: int, user: User):
        if not store.delete(todo_id):
            resp.problem(404, f"Todo {todo_id} does not exist.", todo_id=todo_id)
            return
        resp.no_content(headers={"X-Deleted-Todo": str(todo_id)})

    return api


api = create_api()


if __name__ == "__main__":
    api.run()
