"""Seed dummy demo leads for a CONTROLLED real-call test (Phase A).

This script only CREATES LEADS - it never places a call. The phone number is a
REQUIRED argument so the test number you dial is ALWAYS operator-specified and
never hardcoded. Real calls are placed separately (and gated by
LIVE_CALLS_ENABLED=true + MOCK_MODE=false).

Usage (from repo root, Postgres must be running):
    .\\.venv\\Scripts\\python.exe scripts\\seed_demo_leads.py --phone +9198XXXXXXX
    .\\.venv\\Scripts\\python.exe scripts\\seed_demo_leads.py --phone +9198XXXXXXX --multiple
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("ENVIRONMENT", "development")
# Seeding only creates rows; keep MOCK_MODE on so nothing is accidentally dialed.
os.environ.setdefault("MOCK_MODE", "true")

from app.crud.lead import create_lead  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.schemas.lead import LeadCreate  # noqa: E402

# A handful of realistic dummy scenarios so a single real-call test can exercise
# multiple conversation outcomes from the same test number.
DUMMY_LEADS = [
    {"name": "Arjun Sharma", "source": "demo", "city": "Noida", "notes": "interested in 2BHK near metro; wants early visit"},
    {"name": "Priya Verma", "source": "demo", "city": "Gurgaon", "notes": "compare pricing; 3BHK budget 60 lakh"},
    {"name": "Rahul Gupta", "source": "demo", "city": "Delhi", "notes": "researching; may prefer callback next week"},
]


async def main(phone: str, multiple: bool) -> int:
    if not phone:
        raise SystemExit("error: --phone is required (operator specifies the test number)")
    todos = DUMMY_LEADS if multiple else DUMMY_LEADS[:1]
    async with AsyncSessionLocal() as db:
        for lead in todos:
            record = await create_lead(
                db,
                LeadCreate(
                    name=lead["name"],
                    phone=phone,
                    source=lead["source"],
                    city=lead["city"],
                    notes=lead["notes"],
                ),
            )
            await db.flush()
            print(f"created lead id={record.id} name={lead['name']} phone={phone}")
        await db.commit()
    print(f"\nDone. Created {len(todos)} demo lead(s) for phone {phone}.")
    print("No call was placed. To dial one: create a call against a lead id, then")
    print("set MOCK_MODE=false and LIVE_CALLS_ENABLED=true.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phone", required=True, help="Test phone number (E.164, e.g. +9198XXXXXXXX)")
    parser.add_argument(
        "--multiple",
        action="store_true",
        help="Create all demo scenarios (same number) instead of just the first.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.phone, args.multiple)))
