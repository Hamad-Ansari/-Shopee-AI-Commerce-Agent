import uuid

from backend.database import models

# Event types (every intent, proposal, approval, rejection, execution and error is recorded)
EVENT_INTENT = "intent"
EVENT_SEARCH = "search"
EVENT_PROPOSAL_CREATED = "proposal_created"
EVENT_APPROVAL = "approval"
EVENT_PROPOSAL_APPROVED = "proposal_approved"
EVENT_PROPOSAL_REJECTED = "proposal_rejected"
EVENT_APPROVAL_INVALID = "approval_invalid"
EVENT_REVALIDATED = "revalidated"
EVENT_EXECUTION_STARTED = "execution_started"
EVENT_EXECUTION_SUCCEEDED = "execution_succeeded"
EVENT_EXECUTION_FAILED = "execution_failed"
EVENT_EXECUTION_UNKNOWN = "execution_unknown"
EVENT_STATUS_CHECK = "status_check"
EVENT_POLICY_BLOCKED = "policy_blocked"
EVENT_RATE_LIMITED = "rate_limited"
EVENT_LISTING_DRAFTED = "listing_drafted"
EVENT_LISTING_VALIDATION_FAILED = "listing_validation_failed"
EVENT_LISTING_PUBLISHED = "listing_published"
EVENT_INTEGRATION_UNAVAILABLE = "integration_unavailable"
EVENT_ERROR = "error"
EVENT_USER_REGISTERED = "user_registered"
EVENT_EXECUTION_REPLAYED = "execution_replayed"
EVENT_LLM_FALLBACK = "llm_fallback"
EVENT_WA_INBOUND = "wa_inbound"
EVENT_WA_OUTBOUND = "wa_outbound"
EVENT_WA_BUTTON = "wa_button"
EVENT_WA_SIGNATURE_REJECTED = "wa_signature_rejected"
EVENT_WA_DUPLICATE = "wa_duplicate"


def audit(db, event_type: str, user_id: str | None = None, proposal_id: str | None = None, **details) -> None:
    db.add(
        models.AuditEvent(
            id=uuid.uuid4().hex,
            user_id=user_id,
            event_type=event_type,
            proposal_id=proposal_id,
            details=details,
        )
    )
    db.commit()
