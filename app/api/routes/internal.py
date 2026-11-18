"""Called by machines: n8n (24-hour follow-up) and the Vercel schedule (fallback + demo reset)."""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession, InternalOnly
from app.core.config import settings
from app.models import Lead
from app.services import leads

router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[InternalOnly])


@router.post("/leads/{lead_id}/followup")
def followup_lead(lead_id: int, db: DbSession):
    """n8n calls this after its Wait node. WhatsApp leads get the message from the business number; for the
    others the response carries the text and a WhatsApp link, and n8n alerts the team."""
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")
    return leads.send_followup(db, lead, remind_team=False)


@router.get("/followups")
def run_due_followups(db: DbSession):
    """Without n8n (the live demo), a schedule sends the follow-ups that are due."""
    if "n8n" in settings.lead_sink_set:
        return {"skipped": "n8n handles follow-ups"}
    return {"sent": [leads.send_followup(db, lead) for lead in leads.due_followups(db)]}


@router.get("/reset-demo")
def reset_demo(db: DbSession):
    if not settings.demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a demo")
    from app.seed import reset

    followups = run_due_followups(db)
    return {"reset": reset(db), "followups": followups}
