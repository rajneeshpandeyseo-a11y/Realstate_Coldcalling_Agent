"""Structural tests for the SQLAlchemy models (PHASE 2)."""

from app.models import Base

EXPECTED_TABLES = {
    "users",
    "leads",
    "lead_requirements",
    "calls",
    "call_events",
    "conversation_sessions",
    "conversation_messages",
    "site_visits",
    "follow_ups",
    "campaigns",
    "campaign_leads",
    "agent_configs",
    "provider_configs",
    "cost_records",
    "audit_logs",
}


def test_all_expected_tables_registered():
    """All core domain tables are registered on the shared metadata."""
    registered = set(Base.metadata.tables.keys())
    assert EXPECTED_TABLES <= registered


def test_leads_has_dnc_column():
    """Do-not-call enforcement requires a column on the leads table."""
    from app.models import Lead

    assert "do_not_call" in Lead.__table__.columns


def test_foreign_keys_reference_leads():
    """Core child tables should constrain to leads and cascade."""
    metadata = Base.metadata
    for tbl in ("calls", "site_visits", "follow_ups"):
        fks = {fk.target_fullname for fk in metadata.tables[tbl].foreign_keys}
        assert "leads.id" in fks, f"{tbl} missing FK to leads.id"
    # Events and transcript messages attach to calls (which in turn point at leads)
    for tbl in ("call_events", "conversation_messages", "cost_records"):
        fks = {fk.target_fullname for fk in metadata.tables[tbl].foreign_keys}
        assert "calls.id" in fks, f"{tbl} missing FK to calls.id"
