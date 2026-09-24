"""API v1 router.

Versioned namespace for /api/v1 endpoints. Individual endpoint routers
(leads, calls, site-visits, follow-ups, campaigns, agents, webhooks,
analytics) are attached here as they are implemented in later phases.
"""

from fastapi import APIRouter

from app.api.v1.calls import router as calls_router
from app.api.v1.conversation import router as conversation_router
from app.api.v1.follow_ups import router as follow_ups_router
from app.api.v1.leads import router as leads_router
from app.api.v1.meta import router as meta_router
from app.api.v1.site_visits import router as site_visits_router
from app.api.v1.webhooks import router as webhooks_router
from app.api.v1.ws_stable import router as ws_router

router = APIRouter(prefix="/api/v1")

router.include_router(leads_router)
router.include_router(calls_router)
router.include_router(conversation_router)
router.include_router(site_visits_router)
router.include_router(follow_ups_router)
router.include_router(meta_router)
router.include_router(webhooks_router)
router.include_router(ws_router)
