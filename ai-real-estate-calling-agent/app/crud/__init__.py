"""CRUD package - data-access layer for each resource."""

from app.crud.lead import (
    create_lead,
    get_lead,
    get_lead_by_phone,
    list_leads,
    soft_delete_lead,
    update_lead,
)

__all__ = [
    "create_lead",
    "get_lead",
    "get_lead_by_phone",
    "list_leads",
    "soft_delete_lead",
    "update_lead",
]
