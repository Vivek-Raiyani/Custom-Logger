"""Project management endpoints.

Plan enforcement
----------------
The number of active projects a user may own is capped by
``subscription_plans.max_projects``.  A value of ``-1`` means unlimited.
The limit is read from the DB, so changing a plan's cap takes effect
immediately without any code change.
"""
import re

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select

from app.core.deps import CurrentUser, DB
from app.database.models import Project, UserSubscription

from app.core.security import generate_api_key

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
            is_active=p.is_active,
            created_at=p.created_at.isoformat(),
        )


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
    """Return the max_projects limit for the user's active plan."""
    result = await db.execute(
        select(UserSubscription)
        .where(UserSubscription.user_id == user_id)
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        return 1  # no subscription row → apply most restrictive default

    # Lazy-load plan inline
    from sqlalchemy.orm import selectinload
    result2 = await db.execute(
        select(UserSubscription)
        .where(UserSubscription.user_id == user_id)
        .options(selectinload(UserSubscription.plan))
    )
    sub = result2.scalar_one_or_none()
    return sub.plan.max_projects if sub and sub.plan else 1


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectCreate, current_user: CurrentUser, db: DB) -> ProjectOut:
    max_projects = await _get_max_projects(db, current_user.id)
    current_count = await _active_project_count(db, current_user.id)

    if max_projects != -1 and current_count >= max_projects:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"Your current plan allows a maximum of {max_projects} project(s). "
                "Upgrade your plan to create more."
            ),
        )
    
    raw_key, key_hash = generate_api_key()

    base_slug = _slugify(body.name)

    # Ensure slug uniqueness per user
    slug = base_slug
    suffix = 1
    while True:
        existing = await db.execute(
            select(Project).where(Project.owner_id == current_user.id, Project.slug == slug)
        )
        if existing.scalar_one_or_none() is None:
            break
        slug = f"{base_slug}-{suffix}"
        suffix += 1

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

    print("-"*50)
    print("Need endpoint to get the apikey")
    print("raw_key: ", raw_key) 
    print("-"*50)

    return ProjectOut.from_orm_project(project)


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

    if body.name is not None:
        project.name = body.name.strip()
        project.slug = _slugify(body.name)
    if body.description is not None:
        project.description = body.description

    await db.commit()
    await db.refresh(project)
    return ProjectOut.from_orm_project(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str, current_user: CurrentUser, db: DB) -> None:
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.owner_id == current_user.id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    project.is_active = False  # soft delete
    await db.commit()