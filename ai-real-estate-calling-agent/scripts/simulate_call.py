"""Demo: run a complete outbound call end-to-end (Phase 12).

Cost-free and deterministic: in MOCK_MODE this places no real call and needs no
API key, phone number, balance or KYC. It drives the same CallOrchestrator that
will later place a real Plivo/Sarvam/Gemini call, and prints the full transcript
and cost ledger.

Usage (from repo root, Postgres must be running):
    .\\.venv\\Scripts\\python.exe scripts\\simulate_call.py [--dnc]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Ensure the test database is not used; run against the dev DB in mock mode.
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("MOCK_MODE", "true")

from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: E402

from app.crud.lead import create_lead, get_lead_by_phone  # noqa: E402
from app.db.session import AsyncSessionLocal, Base  # noqa: E402
from app.schemas.call import CallCreate  # noqa: E402
from app.schemas.lead import LeadCreate  # noqa: E402
from app.services import call as call_service  # noqa: E402
from app.services.call_orchestrator import (  # noqa: E402
    CallOrchestrator,
    SCRIPT_DO_NOT_CALL,
)
from app.models.call import Call  # noqa: E402


async def main(dnc: bool) -> int:
    phone = "+919800000000"
    async with AsyncSessionLocal() as db:
        lead = await get_lead_by_phone(db, phone)
        if lead is None:
            lead = await create_lead(
                db,
                LeadCreate(name="Demo Customer", phone=phone, city="Noida"),
            )
            await db.commit()
        else:
            # Re-enable a previously DNC'd demo lead so the demo is repeatable.
            if lead.status == "do_not_call" or lead.do_not_call:
                from app.models.enums import LeadStatus

                lead.do_not_call = False
                lead.opt_out_reason = None
                lead.status = LeadStatus.NEW
                await db.flush()

        call = await call_service.create_call(db, CallCreate(lead_id=lead.id))
        call_id = call.id
        await db.commit()

        factory = async_sessionmaker(
            bind=db.bind,
            class_=type(db),
            expire_on_commit=False,
        )
        # Simulation must NEVER touch paid providers, even when the local
        # .env points at live Plivo/Sarvam/Gemini: force mocks explicitly.
        from app.providers.llm.mock import MockLLMProvider  # noqa: E402
        from app.providers.stt.mock import MockSTTProvider  # noqa: E402
        from app.providers.telephony.mock import MockTelephonyProvider  # noqa: E402
        from app.providers.tts.mock import MockTTSProvider  # noqa: E402

        orch = CallOrchestrator(
            session_factory=factory,
            telephony=MockTelephonyProvider(),
            stt=MockSTTProvider(),
            tts=MockTTSProvider(),
            llm=MockLLMProvider(),
        )
        result = await orch.run_simulated_call(
            db, call, script=SCRIPT_DO_NOT_CALL if dnc else None
        )
        await db.commit()

    print(f"\n=== SIMULATED CALL ({result.provider}) ===")
    print(f"call_id:         {result.call_id}")
    print(f"final status:    {result.final_call_status}")
    print(f"lead status:     {result.final_lead_status}")
    print(f"duration:        {result.duration_seconds}s")
    print("\n--- transcript ---")
    for t in result.turns:
        if t.user_text:
            print(f"CUSTOMER: {t.user_text}")
        mark = " [TERMINAL]" if t.terminal else ""
        if t.reply:
            print(f"AGENT  : {t.reply}{mark}")
        elif not t.user_text:
            print(f"AGENT  : {t.reply}{mark}")
    print("\n--- cost ledger ---")
    c = result.cost
    print(
        f"  stt={c.stt} tts={c.tts} llm={c.llm} telephony={c.telephony} "
        f"total={c.total} ({c.currency})"
    )
    print(f"\nresult: call_id={result.call_id} session_id={result.session_id}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dnc",
        action="store_true",
        help="Run the do-not-call script instead of the interested script.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.dnc)))
