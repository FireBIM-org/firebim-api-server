import os

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import settings

router = APIRouter(tags=["info"])


class BuildInfo(BaseModel):
    git_branch: str
    git_commit: str
    build_time: str
    environment: str


@router.get("/build-info", response_model=BuildInfo)
async def build_info() -> BuildInfo:
    return BuildInfo(
        git_branch=os.getenv("GIT_BRANCH", "Unknown"),
        git_commit=os.getenv("GIT_COMMIT", "Unknown"),
        build_time=os.getenv("BUILD_TIME", "Unknown"),
        environment=settings.ENVIRONMENT,
    )
