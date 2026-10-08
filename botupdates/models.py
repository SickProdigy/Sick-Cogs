"""Pure helpers for BotUpdates draft and delivery policy."""

from urllib.parse import urlsplit

DELIVERY_MODES = frozenset({"major-only", "twice-monthly", "all-approved"})
DRAFT_KINDS = frozenset({"major", "digest", "routine"})
SOURCE_KINDS = frozenset({"project", "upstream"})


def normalize_delivery_mode(value):
    value = str(value).strip().casefold()
    if value not in DELIVERY_MODES:
        raise ValueError("Mode must be major-only, twice-monthly, or all-approved.")
    return value


def normalize_draft_kind(value):
    value = str(value).strip().casefold()
    if value not in DRAFT_KINDS:
        raise ValueError("Draft kind must be major, digest, or routine.")
    return value


def normalize_source(kind, label, url, reference):
    kind = str(kind).strip().casefold()
    if kind not in SOURCE_KINDS:
        raise ValueError("Source kind must be project or upstream.")
    label = " ".join(str(label).split())
    reference = " ".join(str(reference).split())
    url = str(url).strip()
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or any(character.isspace() or character in "<>" for character in url)
    ):
        raise ValueError("Source URL must be a public HTTPS URL without credentials.")
    if not label or len(label) > 100:
        raise ValueError("Source label must contain 1-100 characters.")
    if not reference or len(reference) > 100:
        raise ValueError("Source reference must contain 1-100 characters.")
    if len(url) > 500:
        raise ValueError("Source URL is too long.")
    return {
        "kind": kind,
        "label": label,
        "url": url,
        "reference": reference,
        "key": f"{kind}:{url.casefold()}@{reference.casefold()}",
    }


def should_deliver(mode, draft_kind):
    mode = normalize_delivery_mode(mode)
    draft_kind = normalize_draft_kind(draft_kind)
    if draft_kind == "major":
        return True
    if draft_kind == "digest":
        return mode in {"twice-monthly", "all-approved"}
    return mode == "all-approved"


def new_draft(draft_id, author_id, kind, title, text, created_at):
    kind = normalize_draft_kind(kind)
    title = " ".join(str(title).split())
    text = str(text).strip()
    if not title or len(title) > 250:
        raise ValueError("Title must contain 1-250 characters.")
    if not text or len(text) > 3800:
        raise ValueError("Draft text must contain 1-3,800 characters.")
    return {
        "id": int(draft_id), "kind": kind, "title": title, "text": text,
        "status": "draft", "author_id": int(author_id),
        "created_at": int(created_at), "updated_at": int(created_at),
        "sources": [], "proposals": [], "history": [],
        "approved_by": 0, "approved_at": 0, "delivered_guild_ids": [],
    }


def add_proposal(draft, proposal_id, author_id, text, created_at):
    text = str(text).strip()
    if not text or len(text) > 3800:
        raise ValueError("Proposal text must contain 1-3,800 characters.")
    proposal = {
        "id": int(proposal_id), "author_id": int(author_id), "text": text,
        "created_at": int(created_at), "status": "pending",
    }
    draft.setdefault("proposals", []).append(proposal)
    draft["updated_at"] = int(created_at)
    return proposal


def apply_proposal(draft, proposal_id, publisher_id, applied_at):
    for proposal in draft.get("proposals", []):
        if int(proposal.get("id", 0)) != int(proposal_id):
            continue
        if proposal.get("status") != "pending":
            raise ValueError("That proposal is no longer pending.")
        draft.setdefault("history", []).append({
            "text": draft["text"], "changed_by": int(publisher_id),
            "changed_at": int(applied_at), "proposal_id": int(proposal_id),
        })
        draft["history"] = draft["history"][-25:]
        draft["text"] = proposal["text"]
        draft["updated_at"] = int(applied_at)
        draft["status"] = "draft"
        draft["approved_by"] = 0
        draft["approved_at"] = 0
        proposal["status"] = "applied"
        return proposal
    raise ValueError("Proposal not found.")
