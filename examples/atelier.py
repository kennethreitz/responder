"""A polished Responder API that doubles as a contract-test fixture.

Run it:

    responder run examples/atelier.py

Try it with:

    curl http://127.0.0.1:5042/projects
    curl -H "Authorization: Bearer curator-token" \
         -H "Content-Type: application/json" \
         -d '{"title": "Field Notes", "summary": "A crisp launch brief"}' \
         http://127.0.0.1:5042/projects
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

import responder
from responder.ext.auth import BearerAuth

ProjectStatus = Literal["draft", "published"]


class ProjectIn(BaseModel):
    title: str = Field(min_length=3, examples=["Field Notes"])
    summary: str = Field(min_length=8, examples=["A crisp launch brief"])
    mood: str = Field(default="focused", examples=["focused"])


class ProjectOut(ProjectIn):
    id: int
    slug: str
    status: ProjectStatus
    owner: str


class Principal(BaseModel):
    name: str
    scopes: list[str]


PROJECT_EXAMPLE = {
    "id": 1,
    "title": "Field Notes",
    "summary": "A crisp launch brief",
    "mood": "focused",
    "slug": "field-notes",
    "status": "draft",
    "owner": "Ada",
}

PROJECT_RESPONSE = {
    "description": "Project",
    "content": {
        "application/json": {
            "schema": {"$ref": "#/components/schemas/ProjectOut"},
            "examples": {"field_notes": {"value": PROJECT_EXAMPLE}},
        }
    },
}


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "project"


@dataclass
class ProjectStore:
    projects: dict[int, ProjectOut] = field(default_factory=dict)
    next_id: int = 1

    @classmethod
    def seeded(cls) -> ProjectStore:
        store = cls()
        store.create(
            ProjectIn(
                title="Field Notes",
                summary="A crisp launch brief for the team.",
                mood="focused",
            ),
            owner="Ada",
        )
        store.create(
            ProjectIn(
                title="Signal Room",
                summary="A quiet command center for API operations.",
                mood="calm",
            ),
            owner="Grace",
        )
        store.projects[2] = store.projects[2].model_copy(
            update={"status": "published"}
        )
        return store

    def list(self, *, status: ProjectStatus | None = None) -> list[ProjectOut]:
        rows = sorted(self.projects.values(), key=lambda item: item.id)
        if status is not None:
            rows = [item for item in rows if item.status == status]
        return [item.model_copy(deep=True) for item in rows]

    def get(self, project_id: int) -> ProjectOut | None:
        project = self.projects.get(project_id)
        return None if project is None else project.model_copy(deep=True)

    def create(self, project: ProjectIn, *, owner: str) -> ProjectOut:
        project_id = self.next_id
        self.next_id += 1
        record = ProjectOut(
            id=project_id,
            slug=_slugify(project.title),
            status="draft",
            owner=owner,
            **project.model_dump(),
        )
        self.projects[project_id] = record
        return record.model_copy(deep=True)

    def publish(self, project_id: int) -> ProjectOut | None:
        project = self.projects.get(project_id)
        if project is None:
            return None
        updated = project.model_copy(update={"status": "published"})
        self.projects[project_id] = updated
        return updated.model_copy(deep=True)

    def delete(self, project_id: int) -> bool:
        return self.projects.pop(project_id, None) is not None


def create_api(*, store: ProjectStore | None = None) -> responder.API:
    store = store or ProjectStore.seeded()

    def problem_handler(payload, request, exc):
        payload.setdefault(
            "type",
            f"https://atelier.example/problems/{payload['status']}",
        )
        payload["instance"] = request.url.path
        return payload

    principals = {
        "viewer-token": Principal(name="Lin", scopes=["projects:read"]),
        "writer-token": Principal(
            name="Grace",
            scopes=["projects:read", "projects:write"],
        ),
        "curator-token": Principal(
            name="Ada",
            scopes=["projects:read", "projects:write", "projects:publish"],
        ),
    }

    bearer = BearerAuth(
        verify=lambda token: principals.get(token),
        bearer_format="opaque",
        realm="atelier",
    )

    api = responder.API(
        title="Atelier API",
        version="1.0",
        openapi="3.1.0",
        docs_route="/docs",
        sessions=False,
        problem_handler=problem_handler,
        request_id=True,
    )

    viewer = api.policy("viewer", bearer.optional())
    writer = api.policy("writer", bearer.requires("projects:write"))
    publisher = api.policy("publisher", bearer.requires("projects:publish"))

    @api.get("/", include_in_schema=False)
    def index(req, resp):
        resp.media = {
            "name": "Atelier API",
            "docs": "/docs",
            "schema": "/schema.yml",
        }

    @api.get(
        "/projects",
        auth=viewer,
        operation_id="list_projects",
        tags=["projects"],
        summary="List projects",
        description="Return every project, optionally filtered by status.",
        response_model=list[ProjectOut],
        examples={
            "seed": {"summary": "Seeded projects", "value": [PROJECT_EXAMPLE]}
        },
        openapi_extra={
            "x-codeSamples": [{"lang": "curl", "source": "curl /projects"}]
        },
    )
    def list_projects(
        req,
        resp,
        *,
        status: ProjectStatus | None = responder.Query(
            None,
            description="draft or published",
        ),
    ):
        resp.media = store.list(status=status)

    @api.get(
        "/projects/{project_id:int}",
        auth=viewer,
        operation_id="get_project",
        tags=["projects"],
        summary="Fetch a project",
        response_model=ProjectOut,
        responses={404: "Project not found"},
        examples={"field_notes": {"value": PROJECT_EXAMPLE}},
    )
    def get_project(req, resp, *, project_id: int):
        project = store.get(project_id)
        if project is None:
            resp.problem(
                404,
                f"Project {project_id} does not exist.",
                type="https://atelier.example/problems/project-not-found",
                project_id=project_id,
            )
            return
        resp.media = project

    @api.post(
        "/projects",
        auth=writer,
        operation_id="create_project",
        tags=["projects"],
        summary="Create a project",
        response_model=ProjectOut,
        responses={201: PROJECT_RESPONSE},
        response_examples={
            201: {
                "created": {
                    "summary": "Created project",
                    "value": {
                        **PROJECT_EXAMPLE,
                        "id": 3,
                        "title": "Night Market",
                    },
                }
            }
        },
    )
    def create_project(req, resp, *, user: Principal, project: ProjectIn):
        created = store.create(project, owner=user.name)
        resp.created(created, location=f"/projects/{created.id}")

    @api.post(
        "/projects/{project_id:int}/publish",
        auth=publisher,
        operation_id="publish_project",
        tags=["projects"],
        summary="Publish a project",
        response_model=ProjectOut,
        responses={404: "Project not found", 409: "Project already published"},
        examples={
            "published": {"value": {**PROJECT_EXAMPLE, "status": "published"}}
        },
    )
    def publish_project(req, resp, *, project_id: int, user: Principal):
        project = store.get(project_id)
        if project is None:
            resp.problem(
                404,
                f"Project {project_id} does not exist.",
                type="https://atelier.example/problems/project-not-found",
                project_id=project_id,
            )
            return
        if project.status == "published":
            resp.problem(
                409,
                f"Project {project_id} is already published.",
                type="https://atelier.example/problems/project-already-published",
                project_id=project_id,
            )
            return
        resp.media = store.publish(project_id)

    @api.delete(
        "/projects/{project_id:int}",
        auth=writer,
        operation_id="delete_project",
        tags=["projects"],
        summary="Delete a project",
        responses={204: "Project deleted", 404: "Project not found"},
    )
    def delete_project(req, resp, *, project_id: int, user: Principal):
        if not store.delete(project_id):
            resp.problem(
                404,
                f"Project {project_id} does not exist.",
                type="https://atelier.example/problems/project-not-found",
                project_id=project_id,
            )
            return
        resp.no_content(headers={"X-Deleted-Project": str(project_id)})

    return api


api = create_api()


if __name__ == "__main__":
    api.run()
