"""SQLAlchemy models package.

All models are imported here so that `Base.metadata` is fully populated
(required by Alembic autogenerate) and so that a single import
(`from app.models import ...`) is sufficient.
"""

from app.db.session import Base

from app.models.user import User
from app.models.lead import Lead
from app.models.lead_requirement import LeadRequirement
from app.models.call import Call
from app.models.call_event import CallEvent
from app.models.conversation_session import ConversationSession
from app.models.conversation_message import ConversationMessage
from app.models.site_visit import SiteVisit
from app.models.follow_up import FollowUp
from app.models.campaign import Campaign
from app.models.campaign_lead import CampaignLead
from app.models.agent_config import AgentConfig
from app.models.provider_config import ProviderConfig
from app.models.cost_record import CostRecord
from app.models.audit_log import AuditLog

__all__ = [
    "Base",
    "User",
    "Lead",
    "LeadRequirement",
    "Call",
    "CallEvent",
    "ConversationSession",
    "ConversationMessage",
    "SiteVisit",
    "FollowUp",
    "Campaign",
    "CampaignLead",
    "AgentConfig",
    "ProviderConfig",
    "CostRecord",
    "AuditLog",
]
