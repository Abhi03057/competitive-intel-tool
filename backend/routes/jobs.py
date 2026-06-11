from fastapi import APIRouter
import httpx
from dotenv import load_dotenv
import os
import asyncio
import re

load_dotenv()

SERP_API_KEY = os.getenv("SERP_API_KEY")
SERP_BASE = "https://serpapi.com/search.json"

TECH_KEYWORDS = [
    "Python", "React", "Node.js", "FastAPI", "AWS", "GCP", "Azure",
    "Kubernetes", "Docker", "Kafka", "PostgreSQL", "MongoDB", "Redis",
    "TypeScript", "Go", "Rust", "TensorFlow", "PyTorch", "Spark",
    "Airflow", "dbt", "Tableau", "Power BI", "Java", "C++", "Flutter",
]

ROLE_KEYWORDS = re.compile(
    r"\b(engineer|analyst|developer|manager|intern|associate)\b",
    re.IGNORECASE,
)

router = APIRouter(prefix="/jobs")


def _extract_tech_keywords(descriptions: list[str]) -> list[str]:
    combined = " ".join(descriptions)
    return [kw for kw in TECH_KEYWORDS if kw.lower() in combined.lower()]


def _normalize_location(location: str | None) -> tuple[str, bool]:
    """Return (location_string, location_inferred)."""
    if location:
        return location, False
    return "India", True


async def _fetch_google_jobs(client: httpx.AsyncClient, company_name: str) -> list[dict]:
    params = {
        "engine": "google_jobs",
        "q": f"{company_name} engineer OR analyst OR developer jobs India",
        "api_key": SERP_API_KEY,
    }
    try:
        resp = await client.get(SERP_BASE, params=params, timeout=15.0)
        resp.raise_for_status()
        raw_jobs = resp.json().get("jobs_results", [])
    except Exception:
        return []

    jobs = []
    for j in raw_jobs:
        ext = j.get("detected_extensions", {})
        loc, inferred = _normalize_location(j.get("location"))
        jobs.append({
            "title": j.get("title"),
            "company_name": j.get("company_name"),
            "location": loc,
            "location_inferred": inferred,
            "via": j.get("via"),
            "description": (j.get("description") or "")[:300],
            "schedule_type": ext.get("schedule_type"),
            "salary": ext.get("salary"),
        })
    return jobs


async def _fetch_linkedin_jobs(client: httpx.AsyncClient, company_name: str) -> list[dict]:
    params = {
        "engine": "google",
        "q": f"{company_name} site:linkedin.com/jobs",
        "api_key": SERP_API_KEY,
    }
    try:
        resp = await client.get(SERP_BASE, params=params, timeout=15.0)
        resp.raise_for_status()
        organic = resp.json().get("organic_results", [])
    except Exception:
        return []

    jobs = []
    for r in organic:
        title = r.get("title", "")
        cleaned = title.split(" - ")[0].split(" | ")[0].strip()
        snippet = r.get("snippet", "")

        if not cleaned:
            continue
        if re.search(r"jobs\s+in\s+\w", cleaned, re.IGNORECASE):
            continue
        if re.match(r"^\d", cleaned):
            continue
        if "Leverage your professional network" in snippet or "jobs added daily" in snippet:
            continue

        loc, inferred = _normalize_location(None)
        jobs.append({
            "title": cleaned,
            "company_name": company_name,
            "location": loc,
            "location_inferred": inferred,
            "via": "LinkedIn",
            "description": snippet[:300],
            "schedule_type": None,
            "salary": None,
        })
    return jobs


async def _fetch_google_careers(client: httpx.AsyncClient, company_name: str) -> list[dict]:
    params = {
        "engine": "google",
        "q": f"{company_name} careers jobs 2025 -site:linkedin.com",
        "api_key": SERP_API_KEY,
    }
    try:
        resp = await client.get(SERP_BASE, params=params, timeout=15.0)
        resp.raise_for_status()
        organic = resp.json().get("organic_results", [])
    except Exception:
        return []

    jobs = []
    for r in organic:
        title = r.get("title", "")
        snippet = r.get("snippet", "")
        cleaned = title.split(" | ")[0].split(" - ")[0].strip()

        if not cleaned:
            continue
        if not ROLE_KEYWORDS.search(cleaned) and not ROLE_KEYWORDS.search(snippet):
            continue

        loc, inferred = _normalize_location(None)
        jobs.append({
            "title": cleaned,
            "company_name": company_name,
            "location": loc,
            "location_inferred": inferred,
            "via": "See listing",
            "description": snippet[:300],
            "schedule_type": None,
            "salary": None,
        })
    return jobs


async def fetch_company_jobs(company_name: str) -> dict:
    if not SERP_API_KEY:
        return {"jobs": [], "error": "SERP_API_KEY not configured"}

    try:
        async with httpx.AsyncClient() as client:
            google_jobs, linkedin_jobs, careers_jobs = await asyncio.gather(
                _fetch_google_jobs(client, company_name),
                _fetch_linkedin_jobs(client, company_name),
                _fetch_google_careers(client, company_name),
            )
    except Exception as e:
        return {"jobs": [], "error": str(e)}

    # Merge and deduplicate by normalised title
    seen_titles: set[str] = set()
    merged: list[dict] = []
    for job in google_jobs + linkedin_jobs + careers_jobs:
        key = (job["title"] or "").lower().strip()
        if key and key not in seen_titles:
            seen_titles.add(key)
            merged.append(job)

    descriptions = [j["description"] for j in merged if j["description"]]
    tech_keywords = _extract_tech_keywords(descriptions)

    return {"jobs": merged, "tech_keywords": tech_keywords}


@router.get("/{company_name}")
async def get_company_jobs(company_name: str):
    result = await fetch_company_jobs(company_name)
    jobs = result["jobs"]
    response_jobs = [
        {
            "title": j["title"],
            "location": j["location"],
            "location_inferred": j.get("location_inferred", False),
            "via": j["via"],
            "description": j["description"],
            "schedule_type": j["schedule_type"],
        }
        for j in jobs
    ]
    return {
        "company": company_name,
        "jobs": response_jobs,
        "tech_keywords": result.get("tech_keywords", []),
        "total_jobs": len(response_jobs),
        **({"error": result["error"]} if "error" in result else {}),
    }
