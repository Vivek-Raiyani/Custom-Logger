"""Project management endpoints.

Plan enforcement
----------------
The number of active projects a user may own is capped by
``subscription_plans.max_projects``.  A value of ``-1`` means unlimited.
The limit is read from the DB, so changing a plan's cap takes effect
immediately without any code change.

API key
-------
The raw key is returned ONLY at creation time (POST /projects).
After that, only the prefix (first 16 chars) is visible, and the user
can rotate the key via POST /projects/{id}/rotate-api-key.
"""
import re

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.deps import CurrentUser, DB
from app.core.security import generate_api_key
from app.database.models import Project, UserSubscription
from app.services.pendo import track as pendo_track

router = APIRouter(prefix="/projects", tags=["projects"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Project name cannot be empty")
        return v


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class ProjectOut(BaseModel):
    id: str
    name: str
    slug: str
    description: str | None
    api_key_prefix: str
    is_active: bool
    created_at: str

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_project(cls, p: Project) -> "ProjectOut":
        return cls(
            id=p.id,
            name=p.name,
            slug=p.slug,
            description=p.description,
            api_key_prefix=p.api_key_prefix,
            is_active=p.is_active,
            created_at=p.created_at.isoformat(),
        )


class ProjectCreatedOut(ProjectOut):
    """Returned only on creation — includes the raw API key (shown once)."""
    api_key: str


class ApiKeyRotatedOut(BaseModel):
    api_key: str
    api_key_prefix: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text.strip("-")


async def _active_project_count(db, owner_id: str) -> int:
    result = await db.execute(
        select(func.count()).where(
            Project.owner_id == owner_id,
            Project.is_active == True,  # noqa: E712
        )
    )
    return result.scalar_one()


async def _get_max_projects(db, user_id: str) -> int:
    """Return the max_projects limit for the user's active plan (single query)."""
    result = await db.execute(
        select(UserSubscription)
        .where(UserSubscription.user_id == user_id)
        .options(selectinload(UserSubscription.plan))
    )
    sub = result.scalar_one_or_none()
    if sub is None or sub.plan is None:
        return 1
    return sub.plan.max_projects


async def _ensure_unique_slug(db, owner_id: str, base_slug: str) -> str:
    slug = base_slug
    suffix = 1
    while True:
        existing = await db.execute(
            select(Project).where(Project.owner_id == owner_id, Project.slug == slug)
        )
        if existing.scalar_one_or_none() is None:
            return slug
        slug = f"{base_slug}-{suffix}"
        suffix += 1


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", response_model=ProjectCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate, current_user: CurrentUser, db: DB
) -> ProjectCreatedOut:
    max_projects = await _get_max_projects(db, current_user.id)
    current_count = await _active_project_count(db, current_user.id)

    if max_projects != -1 and current_count >= max_projects:
        await pendo_track(
            "project_limit_reached",
            visitor_id=current_user.id,
            account_id=current_user.id,
            properties={
                "current_project_count": current_count,
                "max_projects": max_projects,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"Your current plan allows a maximum of {max_projects} project(s). "
                "Upgrade your plan to create more."
            ),
        )

    raw_key, key_hash = generate_api_key()
    base_slug = _slugify(body.name)
    slug = await _ensure_unique_slug(db, current_user.id, base_slug)

    project = Project(
        owner_id=current_user.id,
        name=body.name,
        description=body.description,
        slug=slug,
        api_key_hash=key_hash,
        api_key_prefix=raw_key[:16],
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)

    await pendo_track(
        "project_created",
        visitor_id=current_user.id,
        account_id=current_user.id,
        properties={
            "project_id": project.id,
            "project_name": project.name,
            "project_slug": project.slug,
            "has_description": project.description is not None,
        },
    )

    return ProjectCreatedOut(
        id=project.id,
        name=project.name,
        slug=project.slug,
        description=project.description,
        api_key_prefix=project.api_key_prefix,
        is_active=project.is_active,
        created_at=project.created_at.isoformat(),
        api_key=raw_key,  # shown once only
    )


@router.get("", response_model=list[ProjectOut])
async def list_projects(current_user: CurrentUser, db: DB) -> list[ProjectOut]:
    result = await db.execute(
        select(Project)
        .where(Project.owner_id == current_user.id, Project.is_active == True)  # noqa: E712
        .order_by(Project.created_at.desc())
    )
    return [ProjectOut.from_orm_project(p) for p in result.scalars().all()]


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str, current_user: CurrentUser, db: DB) -> ProjectOut:
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.owner_id == current_user.id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return ProjectOut.from_orm_project(project)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: str, body: ProjectUpdate, current_user: CurrentUser, db: DB
) -> ProjectOut:
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.owner_id == current_user.id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    name_changed = body.name is not None
    description_changed = body.description is not None

    if name_changed:
        project.name = body.name.strip()
        project.slug = await _ensure_unique_slug(db, current_user.id, _slugify(body.name))
    if description_changed:
        project.description = body.description

    await db.commit()
    await db.refresh(project)

    await pendo_track(
        "project_updated",
        visitor_id=current_user.id,
        account_id=current_user.id,
        properties={
            "project_id": project.id,
            "name_changed": name_changed,
            "description_changed": description_changed,
        },
    )

    return ProjectOut.from_orm_project(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str, current_user: CurrentUser, db: DB) -> None:
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.owner_id == current_user.id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    project.is_active = False
    await db.commit()

    await pendo_track(
        "project_deleted",
        visitor_id=current_user.id,
        account_id=current_user.id,
        properties={
            "project_id": project.id,
        },
    )


@router.post("/{project_id}/rotate-api-key", response_model=ApiKeyRotatedOut)
async def rotate_api_key(project_id: str, current_user: CurrentUser, db: DB) -> ApiKeyRotatedOut:
    """
    Generate a new API key for the project and invalidate the old one.
    The new raw key is returned once — store it immediately.
    """
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.owner_id == current_user.id,
            Project.is_active.is_(True),
        )
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    raw_key, key_hash = generate_api_key()
    project.api_key_hash = key_hash
    project.api_key_prefix = raw_key[:16]

    await db.commit()

    await pendo_track(
        "api_key_rotated",
        visitor_id=current_user.id,
        account_id=current_user.id,
        properties={
            "project_id": project.id,
        },
    )

    return ApiKeyRotatedOut(api_key=raw_key, api_key_prefix=raw_key[:16])