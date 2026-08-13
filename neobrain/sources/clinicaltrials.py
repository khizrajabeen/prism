"""ClinicalTrials.gov API v2.

Trials are the leading indicator in this field. A phase 1 that quietly changes
status from "Recruiting" to "Active, not recruiting" tells you enrolment closed
months before any paper appears, and a terminated trial tells you something the
sponsor will never publish. That is why `db.upsert_trial` keeps a change log
rather than overwriting the row.
"""

from __future__ import annotations

from typing import Any

from .http import get

BASE = "https://clinicaltrials.gov/api/v2/studies"


def search(
    term: str,
    since: str | None = None,
    *,
    page_size: int = 50,
    max_results: int = 200,
    timeout: float = 30,
    delay: float = 0.34,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "query.term": term,
        "pageSize": min(page_size, 100),
        "format": "json",
    }
    if since:
        params["filter.advanced"] = f"AREA[LastUpdatePostDate]RANGE[{since},MAX]"

    out: list[dict[str, Any]] = []
    token: str | None = None
    while len(out) < max_results:
        if token:
            params["pageToken"] = token
        r = get(BASE, params, timeout=timeout, delay=delay)
        if r is None:
            break
        try:
            data = r.json()
        except ValueError:
            break
        studies = data.get("studies", []) or []
        for s in studies:
            rec = normalize(s)
            if rec:
                out.append(rec)
        token = data.get("nextPageToken")
        if not token or not studies:
            break
    return out[:max_results]


def normalize(study: dict[str, Any]) -> dict[str, Any] | None:
    p = study.get("protocolSection", {}) or {}
    ident = p.get("identificationModule", {}) or {}
    status = p.get("statusModule", {}) or {}
    design = p.get("designModule", {}) or {}
    cond = p.get("conditionsModule", {}) or {}
    arms = p.get("armsInterventionsModule", {}) or {}
    desc = p.get("descriptionModule", {}) or {}
    spon = (p.get("sponsorCollaboratorsModule", {}) or {}).get("leadSponsor", {}) or {}

    nct = ident.get("nctId")
    if not nct:
        return None

    interventions = [
        f"{i.get('type', '')}: {i.get('name', '')}".strip(": ")
        for i in (arms.get("interventions") or [])
    ]

    return {
        "nct_id": nct,
        "title": ident.get("briefTitle", ""),
        "status": status.get("overallStatus", ""),
        "phase": ", ".join(design.get("phases", []) or []),
        "conditions": ", ".join(cond.get("conditions", []) or []),
        "interventions": "; ".join(interventions[:8]),
        "sponsor": spon.get("name", ""),
        "enrollment": (design.get("enrollmentInfo", {}) or {}).get("count"),
        "start_date": (status.get("startDateStruct", {}) or {}).get("date", ""),
        "last_update": (status.get("lastUpdatePostDateStruct", {}) or {}).get("date", ""),
        "url": f"https://clinicaltrials.gov/study/{nct}",
        "summary": (desc.get("briefSummary", "") or "")[:2000],
    }
