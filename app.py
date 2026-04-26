"""
Multi-source Talent Sourcing Portal — Streamlit app

Search sources  : Exa · Google CSE · Apollo · People Data Labs · Crustdata
Enrichment      : FullEnrich · Lusha
Scoring         : Rule-based weighted rubric (100 pts)

Deploy on Streamlit Cloud:
  1. Push to GitHub → share.streamlit.io → New app → app.py
  2. Advanced settings → Secrets — add whichever keys you have:

     APP_PASSWORD        = "your-team-password"
     EXA_API_KEY         = "..."          # exa.ai
     GOOGLE_API_KEY      = "..."          # console.cloud.google.com
     GOOGLE_CSE_ID       = "..."          # programmablesearchengine.google.com
     APOLLO_API_KEY      = "..."          # app.apollo.io
     PDL_API_KEY         = "..."          # peopledatalabs.com
     CRUSTDATA_API_KEY   = "..."          # crustdata.com
     FULLENRICH_API_KEY  = "..."          # fullenrich.com  (enrichment)
     LUSHA_API_KEY       = "..."          # lusha.com       (enrichment)

  At least one search source key is required. All others are optional —
  the app gracefully skips any source without a key.
"""

import re
import urllib.parse
from datetime import date
from io import BytesIO

import pandas as pd
import requests
import streamlit as st

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Talent Sourcing Portal",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Auth gate ─────────────────────────────────────────────────────────────────

def check_password():
    try:
        correct = st.secrets["APP_PASSWORD"]
    except Exception:
        correct = None
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if not st.session_state.authenticated:
        st.markdown("## 🔐 Talent Sourcing Portal")
        st.markdown("Enter the team password to continue.")
        col1, _ = st.columns([2, 3])
        with col1:
            entered = st.text_input("Password", type="password", key="pw_input")
            if st.button("Login", type="primary"):
                if correct and entered == correct:
                    st.session_state.authenticated = True
                    st.rerun()
                elif not correct and entered:
                    st.session_state.authenticated = True
                    st.rerun()
                else:
                    st.error("Incorrect password.")
        st.stop()

check_password()

# ── Secrets helper ────────────────────────────────────────────────────────────

def get_secret(key: str) -> str:
    try:
        raw = st.secrets[key]
    except Exception:
        return ""
    return str(raw).strip().encode("ascii", errors="ignore").decode("ascii")

# ── Scoring constants ─────────────────────────────────────────────────────────

MUST_HAVE_KEYWORDS = {
    "strategic consulting": [
        "strategy consultant", "management consultant", "consulting", "strategic consulting",
        "strategy&", "monitor deloitte", "mckinsey", "bcg", "bain", "roland berger",
        "oliver wyman", "accenture", "kpmg", "pwc", "deloitte", "advanta", "kearney",
        "unternehmensberater", "unternehmensberatung", "strategieberater", "strategie",
        "managementberater", "managementberatung", "berater", "beratung",
    ],
    "project/workstream management": [
        "project manager", "workstream", "project management", "program manager", "pmo",
        "led team", "managed team", "project lead", "engagement manager",
        "projektmanagement", "projektleiter", "projektleitung", "teilprojekt",
    ],
    "client & stakeholder management": [
        "client", "stakeholder", "senior management", "c-suite", "client engagement",
        "client-facing", "executive", "board",
        "kunden", "auftraggeber", "geschäftsführung", "vorstand", "kundenbeziehung",
    ],
    "structured problem-solving": [
        "structured", "problem solving", "analysis", "analytical", "framework",
        "hypothesis", "issue tree", "recommendations",
        "analyse", "konzept", "strukturiert", "problemlösung", "analytisch",
    ],
    "German language": [
        "deutsch", "german", "deutschkenntnisse", "muttersprache",
        "deutschsprachig", "germany", "münchen", "berlin", "frankfurt", "hamburg",
    ],
    "English language": [
        "english", "englisch", "bilingual", "fluent english", "englischkenntnisse",
        "business english",
    ],
}

NICE_TO_HAVE_KEYWORDS = {
    "mentoring / junior development": [
        "mentor", "coached", "junior", "team lead", "leadership", "guided",
        "nachwuchs", "förderung", "teamführung",
    ],
    "entrepreneurial mindset": [
        "entrepreneurial", "startup", "founder", "innovation",
        "unternehmerisch", "gründer", "innovativ",
    ],
    "zero-defect delivery": [
        "quality", "attention to detail", "excellence", "zero defect",
        "qualität", "sorgfalt", "genauigkeit",
    ],
    "empathetic leadership": [
        "empathetic", "empathy", "collaboration", "inclusive",
        "empathie", "zusammenarbeit", "kollegial",
    ],
}

COMPANY_TIER1 = [
    "mckinsey", "bcg", "bain", "roland berger", "oliver wyman", "strategy&",
    "monitor deloitte", "kearney", "arthur d little", "accenture strategy",
    "advanta consulting", "siemens advanta", "boston consulting",
]
COMPANY_TIER2 = [
    "accenture", "deloitte", "pwc", "kpmg", "ey", "capgemini", "porsche consulting",
    "continental", "bosch", "siemens", "mercedes", "volkswagen", "bmw",
    "thyssenkrupp", "basf", "bayer", "allianz",
]
SENIORITY_WORDS = {
    "partner": 5, "vice president": 5, "vp": 5,
    "director": 4, "principal": 4, "geschäftsführer": 4, "direktor": 4,
    "manager": 3, "staff": 3, "lead": 3, "leiter": 3, "projektleiter": 3, "teamleiter": 3,
    "senior": 2, "senior consultant": 2,
    "associate": 1,
    "analyst": 0, "junior": 0, "intern": 0,
}
INDUSTRIAL_WORDS = [
    "industrial", "manufacturing", "automotive", "energy", "siemens", "machinery",
    "engineering", "aerospace", "defense", "logistics", "utilities", "technology",
    "digital transformation", "industrie", "fertigung", "maschinenbau", "energie",
    "technologie", "digitalisierung",
]

# ── JD parser ─────────────────────────────────────────────────────────────────

def parse_jd(jd_text: str, location_filter: str, seniority_opt: str,
             industry_tags: list, extra_must: str) -> dict:
    """Return structured requirements dict from JD text + UI filters."""
    text = jd_text.lower()

    role_titles = []
    for t in ["strategy consultant", "management consultant", "strategic consultant",
              "engagement manager", "principal consultant", "senior advisor",
              "business analyst", "director", "associate"]:
        if t in text:
            role_titles.append(t)
    if not role_titles:
        role_titles = ["strategy consultant", "management consultant"]

    seniority_level_map = {
        "Senior / Manager": 2, "Staff / Principal": 3, "Director / VP": 4, "Any": 2,
    }
    seniority_label_map = {
        "Senior / Manager": "senior", "Staff / Principal": "principal",
        "Director / VP": "director", "Any": "senior",
    }
    seniority_level = seniority_level_map.get(seniority_opt, 2)
    seniority_label = seniority_label_map.get(seniority_opt, "senior")
    # Override from JD text
    if any(w in text for w in ["director", "vp", "vice president"]):
        seniority_label = "director"
    elif any(w in text for w in ["principal", "staff"]):
        seniority_label = "principal"
    elif any(w in text for w in ["senior", "sr."]):
        seniority_label = "senior"

    locations = [l.strip() for l in location_filter.split(",") if l.strip()] or ["Germany"]
    location_country = locations[0].lower()

    skill_signals = []
    for label, kws in [
        ("strategy", ["strategy", "strategic"]),
        ("consulting", ["consulting", "consultant"]),
        ("digital transformation", ["digital transformation", "digitalization"]),
        ("project management", ["project management"]),
        ("industrial", ["industrial", "manufacturing"]),
        ("energy", ["energy", "renewables"]),
    ]:
        if any(k in text for k in kws):
            skill_signals.append(label)

    lang_signals = []
    if any(w in text for w in ["german", "deutsch", "germany"]):
        lang_signals.append("German")
    if any(w in text for w in ["english", "englisch"]):
        lang_signals.append("English")

    query_parts = [seniority_label] + role_titles[:2] + skill_signals[:3] + lang_signals + [locations[0]]
    if extra_must:
        query_parts.append(extra_must)

    return {
        "role_titles": role_titles,
        "seniority_level": seniority_level,
        "seniority_label": seniority_label,
        "locations": locations,
        "location_country": location_country,
        "industries": industry_tags or INDUSTRIAL_WORDS,
        "must_have_skills": list(MUST_HAVE_KEYWORDS.keys()),
        "years_exp_min": 4,
        "query_text": " ".join(query_parts),
    }

# ── Candidate normalisation ───────────────────────────────────────────────────

def norm(overrides: dict) -> dict:
    base = {
        "name": "", "headline": "", "company_name": "", "location": "",
        "linkedin_url": "", "profile_text": "", "source": "", "email": "", "phone": "",
    }
    base.update(overrides)
    return base

# ── Source: Exa ───────────────────────────────────────────────────────────────

def search_exa(query: str, api_key: str, n: int) -> list:
    resp = requests.post(
        "https://api.exa.ai/search",
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
        json={
            "query": query,
            "category": "people",
            "type": "auto",
            "num_results": n,
            "contents": {"text": {"max_characters": 8000}},
        },
        timeout=30,
    )
    resp.raise_for_status()
    out = []
    for r in resp.json().get("results", []):
        url = r.get("url", "")
        text = r.get("text") or ""
        title = r.get("title", "")
        out.append(norm({
            "name": r.get("author") or title.split(" - ")[0].split("|")[0].strip(),
            "headline": title,
            "linkedin_url": url if "linkedin.com/in/" in url else "",
            "profile_text": text[:6000],
            "source": "Exa",
        }))
    return out

# ── Source: Google CSE (X-Ray LinkedIn) ──────────────────────────────────────

def search_google_cse(query: str, api_key: str, cse_id: str, n: int) -> list:
    xray = f'site:linkedin.com/in/ {query}'
    resp = requests.get(
        "https://www.googleapis.com/customsearch/v1",
        params={"key": api_key, "cx": cse_id, "q": xray, "num": min(n, 10)},
        timeout=30,
    )
    resp.raise_for_status()
    out = []
    for item in resp.json().get("items", []):
        title = item.get("title", "")
        name = title.split(" - ")[0].split("|")[0].strip()
        out.append(norm({
            "name": name,
            "headline": title,
            "linkedin_url": item.get("link", ""),
            "profile_text": item.get("snippet", ""),
            "source": "Google CSE",
        }))
    return out

# ── Source: Apollo ────────────────────────────────────────────────────────────

def search_apollo(jd_reqs: dict, api_key: str, n: int) -> list:
    resp = requests.post(
        "https://api.apollo.io/v1/mixed_people_search",
        headers={"Content-Type": "application/json", "Cache-Control": "no-cache"},
        json={
            "api_key": api_key,
            "person_titles": jd_reqs["role_titles"],
            "person_locations": jd_reqs["locations"],
            "page": 1,
            "per_page": min(n, 25),
        },
        timeout=30,
    )
    resp.raise_for_status()
    out = []
    for p in resp.json().get("people", []):
        org = p.get("organization") or {}
        history = p.get("employment_history") or []
        parts = [p.get("title", ""), org.get("name", ""), p.get("city", ""), p.get("country", "")]
        for job in history[:5]:
            t = job.get("title", "")
            co = job.get("organization_name", "")
            if t or co:
                parts.append(f"{t} at {co}".strip(" at"))
        out.append(norm({
            "name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
            "headline": p.get("title", ""),
            "company_name": org.get("name", ""),
            "location": f"{p.get('city', '')} {p.get('country', '')}".strip(),
            "linkedin_url": p.get("linkedin_url") or "",
            "email": p.get("email") or "",
            "profile_text": " | ".join(filter(None, parts)),
            "source": "Apollo",
        }))
    return out

# ── Source: People Data Labs ──────────────────────────────────────────────────

def search_pdl(jd_reqs: dict, api_key: str, n: int) -> list:
    titles_clause = " OR ".join(
        f"job_title LIKE '%{t}%'" for t in jd_reqs["role_titles"][:3]
    )
    country = jd_reqs["location_country"]
    sql = (
        f"SELECT * FROM person WHERE ({titles_clause}) "
        f"AND location_country='{country}' LIMIT {n}"
    )
    resp = requests.get(
        "https://api.peopledatalabs.com/v5/person/search",
        headers={"X-Api-Key": api_key},
        params={"sql": sql, "size": n},
        timeout=30,
    )
    resp.raise_for_status()
    out = []
    for p in resp.json().get("data", []):
        experience = p.get("experience") or []
        parts = [p.get("job_title", ""), p.get("job_company_name", ""), p.get("location_name", "")]
        for exp in experience[:5]:
            t = (exp.get("title") or {}).get("name", "")
            co = (exp.get("company") or {}).get("name", "")
            if t or co:
                parts.append(f"{t} at {co}".strip(" at"))
        skills = [s.get("name", "") for s in (p.get("skills") or [])]
        if skills:
            parts.append("Skills: " + ", ".join(skills[:10]))
        li_id = p.get("linkedin_id", "")
        li_url = p.get("linkedin_url") or (f"https://linkedin.com/in/{li_id}" if li_id else "")
        emails = p.get("emails") or [{}]
        out.append(norm({
            "name": p.get("full_name", ""),
            "headline": p.get("job_title", ""),
            "company_name": p.get("job_company_name", ""),
            "location": p.get("location_name", ""),
            "linkedin_url": li_url,
            "email": emails[0].get("address", ""),
            "profile_text": " | ".join(filter(None, parts)),
            "source": "PDL",
        }))
    return out

# ── Source: Crustdata ─────────────────────────────────────────────────────────

def search_crustdata(jd_reqs: dict, api_key: str, n: int) -> list:
    resp = requests.post(
        "https://api.crustdata.com/screener/person/search",
        headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json"},
        json={
            "filters": {
                "job_title": jd_reqs["role_titles"][0],
                "location": jd_reqs["locations"][0],
            },
            "page": 1,
            "limit": n,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    people = data.get("data") or data.get("profiles") or data.get("results") or []
    out = []
    for p in people:
        out.append(norm({
            "name": p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
            "headline": p.get("headline") or p.get("title", ""),
            "company_name": p.get("company") or p.get("current_company", ""),
            "location": p.get("location", ""),
            "linkedin_url": p.get("linkedin_url", ""),
            "profile_text": p.get("summary") or p.get("headline", ""),
            "source": "Crustdata",
        }))
    return out

# ── Enrichment: FullEnrich ────────────────────────────────────────────────────

def enrich_fullenrich(candidates: list, api_key: str) -> list:
    enriched = []
    for c in candidates:
        if not c.get("linkedin_url"):
            enriched.append(c)
            continue
        try:
            resp = requests.post(
                "https://api.fullenrich.com/v1/enrich/person",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"linkedin_url": c["linkedin_url"]},
                timeout=15,
            )
            if resp.ok:
                d = resp.json().get("person") or {}
                if d.get("full_name"):
                    c["name"] = d["full_name"]
                if d.get("headline"):
                    c["headline"] = d["headline"]
                if d.get("email"):
                    c["email"] = d["email"]
                if d.get("summary"):
                    c["profile_text"] = (c["profile_text"] + " " + d["summary"]).strip()
                c["source"] += " +FullEnrich"
        except Exception:
            pass
        enriched.append(c)
    return enriched

# ── Enrichment: Lusha ─────────────────────────────────────────────────────────

def enrich_lusha(candidates: list, api_key: str) -> list:
    enriched = []
    for c in candidates:
        parts = (c.get("name") or "").split()
        if len(parts) < 2 or not c.get("company_name"):
            enriched.append(c)
            continue
        try:
            resp = requests.get(
                "https://api.lusha.com/person",
                headers={"api_key": api_key},
                params={
                    "firstName": parts[0],
                    "lastName": " ".join(parts[1:]),
                    "company": c["company_name"],
                },
                timeout=15,
            )
            if resp.ok:
                d = resp.json()
                emails = d.get("emailAddresses") or []
                phones = d.get("phoneNumbers") or []
                if emails:
                    c["email"] = emails[0].get("emailAddress", c.get("email", ""))
                if phones:
                    c["phone"] = phones[0].get("localizedPhoneNumber", "")
                c["source"] += " +Lusha"
        except Exception:
            pass
        enriched.append(c)
    return enriched

# ── Merge & Deduplicate ───────────────────────────────────────────────────────

def merge_candidates(candidate_lists: list) -> list:
    all_cands = [c for lst in candidate_lists for c in lst]
    seen_li, seen_names, merged = set(), set(), []
    for c in all_cands:
        li = (c.get("linkedin_url") or "").rstrip("/").lower()
        name = (c.get("name") or "").lower().strip()
        if li and li in seen_li:
            # Merge into existing record
            for ex in merged:
                if (ex.get("linkedin_url") or "").rstrip("/").lower() == li:
                    if c["source"] not in ex["source"]:
                        ex["source"] += f", {c['source']}"
                    if len(c.get("profile_text", "")) > len(ex.get("profile_text", "")):
                        ex["profile_text"] = c["profile_text"]
                    if not ex.get("email") and c.get("email"):
                        ex["email"] = c["email"]
                    if not ex.get("company_name") and c.get("company_name"):
                        ex["company_name"] = c["company_name"]
                    break
            continue
        if name and name in seen_names and not li:
            continue
        if li:
            seen_li.add(li)
        if name:
            seen_names.add(name)
        merged.append(c)
    return merged

# ── Scoring ───────────────────────────────────────────────────────────────────

def score_candidate(profile_text: str, jd_reqs: dict) -> dict:
    """Score against JD requirements. All points must be earned."""
    text = (profile_text or "").lower()

    # 1. Must-have skills (35 pts)
    must_haves = jd_reqs.get("must_have_skills", list(MUST_HAVE_KEYWORDS.keys()))
    mh_hits = {
        skill: any(kw in text for kw in MUST_HAVE_KEYWORDS.get(skill, [skill.lower()]))
        for skill in must_haves
    }
    mh_score = round(35 * sum(mh_hits.values()) / max(len(mh_hits), 1))

    # 2. Seniority (15 pts)
    target = jd_reqs.get("seniority_level", 2)
    detected = max((level for word, level in SENIORITY_WORDS.items() if word in text), default=0)
    diff = abs(detected - target)
    seniority_score = 15 if diff == 0 else (8 if diff == 1 else 0)

    # 3. Experience years (10 pts)
    years = [int(y) for y in re.findall(r'(\d+)\s*(?:year|yr)', text)]
    max_years = max(years, default=0)
    min_exp = jd_reqs.get("years_exp_min", 4)
    exp_score = 10 if max_years >= min_exp else (7 if max_years >= min_exp - 1 else 3)

    # 4. Nice-to-have (15 pts)
    nth_hits = {skill: any(kw in text for kw in kws) for skill, kws in NICE_TO_HAVE_KEYWORDS.items()}
    nth_score = round(15 * sum(nth_hits.values()) / max(len(nth_hits), 1))

    # 5. Industry (10 pts)
    industries = jd_reqs.get("industries", INDUSTRIAL_WORDS)
    if any(ind.lower() in text for ind in industries):
        industry_score = 10
    elif any(w in text for w in INDUSTRIAL_WORDS):
        industry_score = 5
    else:
        industry_score = 0

    # 6. Company signal (10 pts)
    company_signal = (
        10 if any(c in text for c in COMPANY_TIER1) else
        5 if any(c in text for c in COMPANY_TIER2) else 0
    )

    # 7. Location (5 pts)
    locs = [l.lower() for l in jd_reqs.get("locations", ["germany"])]
    location_signal = 5 if any(l in text for l in locs) else (3 if "remote" in text else 0)

    # Data quality penalty
    wc = len(text.split())
    quality_penalty = 10 if wc < 50 else (3 if wc < 150 else 0)
    quality = "sparse" if wc < 50 else ("partial" if wc < 150 else "full")

    total = (mh_score + seniority_score + exp_score + nth_score +
             industry_score + company_signal + location_signal - quality_penalty)

    return {
        "must_have_detail": mh_hits,
        "must_have": mh_score,
        "seniority": seniority_score,
        "experience": exp_score,
        "nice_to_have": nth_score,
        "industry": industry_score,
        "company_signal": company_signal,
        "location_signal": location_signal,
        "quality_penalty": quality_penalty,
        "data_quality": quality,
        "total": max(0, min(100, total)),
    }


def grade_label(score: int) -> str:
    if score >= 75:
        return "🟢 Strong Match"
    if score >= 50:
        return "🟡 Potential"
    return "🔴 Weak Match"

# ── Boolean string builder ────────────────────────────────────────────────────

def build_boolean_string(jd_text: str, refinement: str = "") -> str:
    text = jd_text.lower()
    ref = refinement.lower()

    seniority_terms = []
    if any(w in text for w in ["senior", "sr."]):
        seniority_terms += ['"senior"', '"sr."']
    if "manager" in text:
        seniority_terms.append('"manager"')
    if "director" in text:
        seniority_terms.append('"director"')
    if not seniority_terms:
        seniority_terms = ['"senior"', '"manager"', '"lead"']

    if "mbb" in ref or any(w in ref for w in ["mckinsey", "bcg", "bain"]):
        company_filter = '("McKinsey" OR "BCG" OR "Bain")'
    elif "big4" in ref or "big 4" in ref or "deloitte" in ref:
        company_filter = '("Deloitte" OR "PwC" OR "KPMG" OR "EY")'
    elif "siemens" in ref:
        company_filter = '"Siemens"'
    else:
        company_filter = '("Siemens" OR "McKinsey" OR "BCG" OR "Bain" OR "Deloitte" OR "Accenture" OR "Roland Berger")'

    exclude = "-recruiter -intern -junior"
    if "no automotive" in ref or "remove automotive" in ref:
        exclude += " -automotive -Porsche -BMW -Daimler"

    lang_filter = '("German" OR "Deutsch")' if "german" in text else ""

    parts = [
        'site:linkedin.com/in/',
        '("strategy consultant" OR "management consultant" OR "strategic consultant")',
        f'({" OR ".join(seniority_terms)})',
    ]
    if lang_filter:
        parts.append(lang_filter)
    parts += [company_filter, exclude]
    return " ".join(parts)

# ── Excel export ──────────────────────────────────────────────────────────────

def build_excel_bytes(df: pd.DataFrame, jd_text: str, boolean_str: str) -> bytes:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter

        wb = Workbook()
        ws = wb.active
        ws.title = "Candidates"

        headers = ["Rank", "Score", "Grade", "Name", "Headline", "Company",
                   "Location", "LinkedIn URL", "Email", "Phone", "Source",
                   "Must-Have Skills", "Data Quality", "Profile Highlights"]
        col_widths = [5, 7, 18, 25, 35, 25, 20, 30, 28, 16, 20, 45, 12, 50]

        hfill = PatternFill("solid", fgColor="1F3864")
        hfont = Font(bold=True, color="FFFFFF", size=11)
        green  = PatternFill("solid", fgColor="C6EFCE")
        yellow = PatternFill("solid", fgColor="FFEB9C")
        red    = PatternFill("solid", fgColor="FFC7CE")
        wrap   = Alignment(wrap_text=True, vertical="top")
        center = Alignment(horizontal="center", vertical="center")

        ws.append(headers)
        for i, (cell, w) in enumerate(zip(ws[1], col_widths), 1):
            cell.fill = hfill
            cell.font = hfont
            cell.alignment = center
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

        for _, row in df.iterrows():
            score = int(row.get("score", 0))
            fill = green if score >= 75 else (yellow if score >= 50 else red)
            mh = row.get("must_have_detail", {})
            mh_str = ", ".join(f"{k} {'✓' if v else '✗'}" for k, v in mh.items()) if isinstance(mh, dict) else ""
            ws.append([
                int(row.get("rank", 0)),
                score,
                grade_label(score),
                str(row.get("name", "")),
                str(row.get("headline", "")),
                str(row.get("company_name", "")),
                str(row.get("location", "")),
                str(row.get("linkedin_url", "")),
                str(row.get("email", "")),
                str(row.get("phone", "")),
                str(row.get("source", "")),
                mh_str,
                str(row.get("data_quality", "")),
                str(row.get("profile_text", ""))[:400],
            ])
            ri = ws.max_row
            for cell in ws[ri]:
                cell.fill = fill
                cell.alignment = wrap
            ws.row_dimensions[ri].height = 55

        ws2 = wb.create_sheet("Search Artefacts")
        ws2.column_dimensions["A"].width = 25
        ws2.column_dimensions["B"].width = 90
        bold = Font(bold=True)
        for label, val in [
            ("Generated On", str(date.today())),
            ("Boolean X-Ray String", boolean_str),
            ("JD Summary", jd_text[:800]),
        ]:
            ws2.append([label, val])
            ws2[f"A{ws2.max_row}"].font = bold
            ws2[f"B{ws2.max_row}"].alignment = Alignment(wrap_text=True)
            ws2.row_dimensions[ws2.max_row].height = 40

        buf = BytesIO()
        wb.save(buf)
        return buf.getvalue()
    except Exception:
        buf = BytesIO()
        df.to_csv(buf, index=False)
        return buf.getvalue()

# ── Session state ─────────────────────────────────────────────────────────────

for _k in ["results_df", "source_stats", "boolean_str", "search_query", "jd_text"]:
    if _k not in st.session_state:
        st.session_state[_k] = None if _k in ["results_df", "source_stats"] else ""

# ── Load all secrets ──────────────────────────────────────────────────────────

EXA_KEY        = get_secret("EXA_API_KEY")
GOOGLE_KEY     = get_secret("GOOGLE_API_KEY")
GOOGLE_CSE_ID  = get_secret("GOOGLE_CSE_ID")
APOLLO_KEY     = get_secret("APOLLO_API_KEY")
PDL_KEY        = get_secret("PDL_API_KEY")
CRUSTDATA_KEY  = get_secret("CRUSTDATA_API_KEY")
FULLENRICH_KEY = get_secret("FULLENRICH_API_KEY")
LUSHA_KEY      = get_secret("LUSHA_API_KEY")

# (api_key_or_bool, display_name, secret_hint)
SEARCH_SOURCES = [
    (EXA_KEY,                          "Exa",        "EXA_API_KEY"),
    (GOOGLE_KEY and GOOGLE_CSE_ID,     "Google CSE", "GOOGLE_API_KEY + GOOGLE_CSE_ID"),
    (APOLLO_KEY,                       "Apollo",     "APOLLO_API_KEY"),
    (PDL_KEY,                          "PDL",        "PDL_API_KEY"),
    (CRUSTDATA_KEY,                    "Crustdata",  "CRUSTDATA_API_KEY"),
]
ENRICH_SOURCES = [
    (FULLENRICH_KEY, "FullEnrich", "FULLENRICH_API_KEY"),
    (LUSHA_KEY,      "Lusha",      "LUSHA_API_KEY"),
]
SOURCE_COLORS = {
    "Exa": "🔵", "Google CSE": "🔴", "Apollo": "🟠", "PDL": "🟣", "Crustdata": "🟤",
}

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## ⚙️ Settings")

    st.markdown("### 🔌 Search Sources")
    active_count = 0
    for key, name, hint in SEARCH_SOURCES:
        if key:
            st.caption(f"✅ **{SOURCE_COLORS.get(name, '⚫')} {name}** — active")
            active_count += 1
        else:
            st.caption(f"⬜ **{name}** — add `{hint}` to secrets")

    st.markdown("### 🧬 Enrichment")
    for key, name, hint in ENRICH_SOURCES:
        if key:
            st.caption(f"✅ **{name}** — active")
        else:
            st.caption(f"⬜ **{name}** — add `{hint}` to secrets")

    st.divider()
    num_results = st.slider("Results per source", 5, 50, 20)
    enrich_top_n = st.slider(
        "Enrich top N candidates", 0, 20, 0, 5,
        help="Run FullEnrich / Lusha on the top-ranked candidates after initial scoring. 0 = skip.",
    )

    st.divider()
    st.markdown("### 📖 About")
    st.caption(
        "Multi-source talent search: Exa · Google CSE · Apollo · PDL · Crustdata.  \n"
        "Enrichment: FullEnrich · Lusha.  \n"
        "Results are deduplicated and scored on a 100-point rubric."
    )

# ── Main UI ───────────────────────────────────────────────────────────────────

st.markdown("# 🔍 Talent Sourcing Portal")
st.caption("Paste a JD → search across multiple sources → deduplicate & score → export")

tab_search, tab_results, tab_string = st.tabs(["📋 Search", "📊 Results", "🔧 Search String"])

# ── TAB 1: Search ─────────────────────────────────────────────────────────────

with tab_search:
    col1, col2 = st.columns([3, 2])

    with col1:
        jd_text = st.text_area(
            "Job Description",
            height=300,
            placeholder="Paste the full JD here...",
            value=st.session_state.jd_text,
        )

    with col2:
        st.markdown("#### Quick Filters")
        location_filter = st.text_input("Location", value="Germany", placeholder="Germany, Munich...")
        seniority_opt = st.selectbox(
            "Seniority", ["Senior / Manager", "Staff / Principal", "Director / VP", "Any"]
        )
        industry_tags = st.multiselect(
            "Industries",
            ["industrial", "technology", "energy", "automotive", "fintech", "healthcare", "manufacturing"],
            default=["industrial", "technology", "energy"],
        )
        extra_must = st.text_input("Additional keyword", placeholder="e.g. MBA, fluent German")
        st.markdown("#### Refinement")
        refinement = st.text_input(
            "Refine search (natural language)",
            placeholder='e.g. "MBB alumni only" or "remove automotive"',
        )

    # Preview boolean string before running
    if jd_text.strip():
        preview_bool = build_boolean_string(jd_text, refinement)
        with st.expander("🔍 Preview & edit Boolean / X-Ray string before running", expanded=False):
            st.text_area(
                "Google X-Ray string (used by Google CSE source)",
                value=preview_bool, height=80, key="preview_bool",
            )
            st.caption(
                "Exa, Apollo, and PDL use a structured query derived from the JD text. "
                "The Boolean string above is used for the Google CSE LinkedIn X-Ray search."
            )

    if active_count == 0:
        st.warning("⚠️ No API keys configured. Add at least `EXA_API_KEY` to Streamlit secrets.")

    if st.button("🚀 Run Search", type="primary", use_container_width=True):
        if not jd_text.strip():
            st.error("Please paste a Job Description first.")
        elif active_count == 0:
            st.error("No API keys configured.")
        else:
            st.session_state.jd_text = jd_text
            jd_reqs = parse_jd(jd_text, location_filter, seniority_opt, industry_tags, extra_must)
            boolean_str = build_boolean_string(jd_text, refinement)
            st.session_state.boolean_str = boolean_str
            st.session_state.search_query = jd_reqs["query_text"]

            source_stats: dict = {}
            all_results: list = []
            active_sources = [(key, name) for key, name, _ in SEARCH_SOURCES if key]
            progress = st.progress(0, text="Starting multi-source search...")

            for i, (key, name) in enumerate(active_sources):
                progress.progress(i / len(active_sources), text=f"Searching {name}…")
                try:
                    if name == "Exa":
                        results = search_exa(jd_reqs["query_text"], EXA_KEY, num_results)
                    elif name == "Google CSE":
                        results = search_google_cse(jd_reqs["query_text"], GOOGLE_KEY, GOOGLE_CSE_ID, num_results)
                    elif name == "Apollo":
                        results = search_apollo(jd_reqs, APOLLO_KEY, num_results)
                    elif name == "PDL":
                        results = search_pdl(jd_reqs, PDL_KEY, num_results)
                    elif name == "Crustdata":
                        results = search_crustdata(jd_reqs, CRUSTDATA_KEY, num_results)
                    else:
                        results = []
                    source_stats[name] = len(results)
                    all_results.append(results)
                except Exception as e:
                    st.warning(f"⚠️ {name} error: {e}")
                    source_stats[name] = 0
                    all_results.append([])

            progress.progress(0.85, text="Merging & deduplicating…")
            merged = merge_candidates(all_results)

            # Enrichment: score first to pick top N, then enrich, then re-score rest
            if enrich_top_n > 0 and (FULLENRICH_KEY or LUSHA_KEY):
                for c in merged:
                    s = score_candidate(c["profile_text"], jd_reqs)
                    c["_pre_score"] = s["total"]
                merged.sort(key=lambda x: x.get("_pre_score", 0), reverse=True)
                top, rest = merged[:enrich_top_n], merged[enrich_top_n:]
                if FULLENRICH_KEY:
                    progress.progress(0.90, text="Enriching with FullEnrich…")
                    top = enrich_fullenrich(top, FULLENRICH_KEY)
                if LUSHA_KEY:
                    progress.progress(0.95, text="Enriching with Lusha…")
                    top = enrich_lusha(top, LUSHA_KEY)
                merged = top + rest

            progress.progress(0.97, text="Scoring…")
            candidates = []
            for c in merged:
                s = score_candidate(c["profile_text"], jd_reqs)
                c.update({
                    "must_have_detail": s["must_have_detail"],
                    "must_have":        s["must_have"],
                    "seniority":        s["seniority"],
                    "experience":       s["experience"],
                    "nice_to_have":     s["nice_to_have"],
                    "industry":         s["industry"],
                    "company_signal":   s["company_signal"],
                    "location_signal":  s["location_signal"],
                    "quality_penalty":  s["quality_penalty"],
                    "data_quality":     s["data_quality"],
                    "score":            s["total"],
                    "grade":            grade_label(s["total"]),
                })
                candidates.append(c)

            df = pd.DataFrame(candidates).sort_values("score", ascending=False).reset_index(drop=True)
            df["rank"] = df.index + 1
            st.session_state.results_df = df
            st.session_state.source_stats = source_stats
            progress.empty()

            stats_str = " · ".join(f"{k}: {v}" for k, v in source_stats.items())
            st.success(
                f"✅ **{len(df)} unique candidates** across {len(active_sources)} sources "
                f"({stats_str}). Switch to the **Results** tab."
            )

# ── TAB 2: Results ────────────────────────────────────────────────────────────

with tab_results:
    df = st.session_state.results_df

    if df is None:
        st.info("Run a search first to see results here.")
    else:
        # Per-source breakdown
        if st.session_state.source_stats:
            src_items = list(st.session_state.source_stats.items())
            cols = st.columns(len(src_items))
            for col, (src, cnt) in zip(cols, src_items):
                col.metric(f"{SOURCE_COLORS.get(src, '⚫')} {src}", cnt)
            st.divider()

        # Grade summary
        strong    = int((df["score"] >= 75).sum())
        potential = int(((df["score"] >= 50) & (df["score"] < 75)).sum())
        weak      = int((df["score"] < 50).sum())
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total (deduplicated)", len(df))
        m2.metric("🟢 Strong Match", strong)
        m3.metric("🟡 Potential", potential)
        m4.metric("🔴 Weak Match", weak)
        st.divider()

        grade_filter = st.multiselect(
            "Filter by grade",
            ["🟢 Strong Match", "🟡 Potential", "🔴 Weak Match"],
            default=["🟢 Strong Match", "🟡 Potential"],
        )
        filtered_df = df[df["grade"].isin(grade_filter)] if grade_filter else df

        display_cols = ["rank", "score", "grade", "name", "headline", "company_name",
                        "location", "email", "phone", "source", "data_quality", "linkedin_url"]
        available = [c for c in display_cols if c in filtered_df.columns]

        st.dataframe(
            filtered_df[available].rename(columns={
                "rank": "Rank", "score": "Score", "grade": "Grade",
                "name": "Name", "headline": "Headline", "company_name": "Company",
                "location": "Location", "email": "Email", "phone": "Phone",
                "source": "Source", "data_quality": "Data Quality", "linkedin_url": "LinkedIn",
            }),
            use_container_width=True,
            height=420,
            column_config={
                "LinkedIn": st.column_config.LinkColumn("LinkedIn"),
                "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100),
            },
            hide_index=True,
        )

        st.divider()
        st.markdown("#### 📋 Candidate Details")
        for _, row in filtered_df.head(10).iterrows():
            score = int(row.get("score", 0))
            with st.expander(
                f"{row.get('grade', '')} **{row.get('name', 'Unknown')}** — "
                f"{score}/100 | {row.get('headline', '')} | _{row.get('source', '')}_"
            ):
                c1, c2 = st.columns([2, 1])
                with c1:
                    li = row.get("linkedin_url", "")
                    if li:
                        st.markdown(f"**LinkedIn:** [{li}]({li})")
                    em = row.get("email", "")
                    if em:
                        st.markdown(f"**Email:** {em}")
                    ph = row.get("phone", "")
                    if ph:
                        st.markdown(f"**Phone:** {ph}")
                    st.markdown(
                        f"**Company:** {row.get('company_name', '—')}  "
                        f"| **Location:** {row.get('location', '—')}"
                    )
                    st.markdown("**Profile text:**")
                    st.caption(str(row.get("profile_text", ""))[:800])
                with c2:
                    st.markdown("**Score breakdown:**")
                    mh_detail = row.get("must_have_detail", {})
                    if isinstance(mh_detail, dict):
                        for skill, hit in mh_detail.items():
                            st.write(f"{'✅' if hit else '❌'} {skill}")
                    st.metric("Must-Have", f"{row.get('must_have', 0)}/35")
                    st.metric("Seniority", f"{row.get('seniority', 0)}/15")
                    st.metric("Industry", f"{row.get('industry', 0)}/10")
                    st.metric("Company Signal", f"{row.get('company_signal', 0)}/10")

        st.divider()
        st.markdown("#### 📥 Export")
        col_a, col_b = st.columns(2)
        with col_a:
            excel_bytes = build_excel_bytes(
                filtered_df, st.session_state.jd_text, st.session_state.boolean_str
            )
            st.download_button(
                "⬇️ Download Excel", data=excel_bytes,
                file_name=f"candidates_{date.today()}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True, type="primary",
            )
        with col_b:
            st.download_button(
                "⬇️ Download CSV",
                data=filtered_df[available].to_csv(index=False),
                file_name=f"candidates_{date.today()}.csv",
                mime="text/csv", use_container_width=True,
            )

# ── TAB 3: Search String ──────────────────────────────────────────────────────

with tab_string:
    st.markdown("### Boolean / X-Ray Search String")
    st.caption("Used by Google CSE for LinkedIn X-Ray. Copy into Google for a manual search.")

    boolean_str = st.text_area(
        "Search String (editable)",
        value=st.session_state.boolean_str or "Run a search first to generate the string.",
        height=120,
        key="editable_boolean",
    )
    if boolean_str != st.session_state.boolean_str:
        st.session_state.boolean_str = boolean_str

    st.markdown("#### Last structured query (Exa / Apollo / PDL)")
    st.code(st.session_state.search_query or "(not yet run)", language="text")

    st.divider()
    if st.session_state.boolean_str:
        encoded = urllib.parse.quote(st.session_state.boolean_str)
        st.markdown(f"[🔗 Open X-Ray search in Google](https://www.google.com/search?q={encoded})")

    st.divider()
    st.markdown("#### Refinement Suggestions")
    st.markdown("""
| Intent | What to type in "Refine search" |
|---|---|
| MBB alumni only | `focus on MBB background only` |
| Remove automotive | `remove automotive, focus on energy` |
| German native speakers | `German native speakers only` |
| More senior | `only Principal or Director level` |
| Big4 consulting | `Big4 or top-tier consulting firms` |
| Specific city | `Munich only` |
    """)
