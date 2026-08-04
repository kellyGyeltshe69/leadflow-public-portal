from fastapi import APIRouter

from . import admin, analytics, auth, businesses, campaigns

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router)
router.include_router(campaigns.router)
router.include_router(businesses.router)
router.include_router(analytics.router)
router.include_router(admin.router)
