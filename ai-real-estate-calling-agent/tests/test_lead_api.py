"""Integration tests for the Lead CRM API (PHASE 3)."""

import pytest


@pytest.mark.asyncio
async def test_create_and_get_lead(client):
    resp = await client.post(
        "/api/v1/leads",
        json={"name": "Rahul", "phone": "+919800000001", "city": "Mumbai"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["phone"] == "+919800000001"
    assert data["status"] == "new"
    assert data["do_not_call"] is False
    assert data["id"]

    lead_id = data["id"]
    get_resp = await client.get(f"/api/v1/leads/{lead_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "Rahul"


@pytest.mark.asyncio
async def test_create_duplicate_phone_conflict(client):
    payload = {"name": "A", "phone": "+919800000002"}
    assert (await client.post("/api/v1/leads", json=payload)).status_code == 201
    assert (await client.post("/api/v1/leads", json=payload)).status_code == 409


@pytest.mark.asyncio
async def test_list_leads_pagination_and_filter(client):
    for i in range(3, 6):
        await client.post(
            "/api/v1/leads",
            json={"name": f"Lead{i}", "phone": f"+9198000000{i}"},
        )

    resp = await client.get("/api/v1/leads?page=1&page_size=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert data["page_size"] == 2
    assert len(data["items"]) == 2
    assert data["pages"] == 2

    filtered = await client.get("/api/v1/leads?search=Lead4")
    assert filtered.json()["total"] == 1


@pytest.mark.asyncio
async def test_update_lead(client):
    created = (await client.post(
        "/api/v1/leads", json={"name": "Before", "phone": "+919800000099"}
    )).json()
    resp = await client.patch(
        f"/api/v1/leads/{created['id']}", json={"name": "After", "city": "Delhi"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "After"
    assert body["city"] == "Delhi"


@pytest.mark.asyncio
async def test_do_not_call_enforcement(client):
    created = (await client.post(
        "/api/v1/leads", json={"name": "Opt", "phone": "+919800000077"}
    )).json()
    resp = await client.post(
        f"/api/v1/leads/{created['id']}/do-not-call", params={"reason": "requested"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["do_not_call"] is True
    assert body["status"] == "do_not_call"


@pytest.mark.asyncio
async def test_soft_delete_lead(client):
    created = (await client.post(
        "/api/v1/leads", json={"name": "Del", "phone": "+919800000088"}
    )).json()
    lead_id = created["id"]

    del_resp = await client.delete(f"/api/v1/leads/{lead_id}")
    assert del_resp.status_code == 204

    get_resp = await client.get(f"/api/v1/leads/{lead_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_get_missing_lead_404(client):
    resp = await client.get("/api/v1/leads/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
