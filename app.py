"""
Talent Sourcing Portal — Streamlit app
Powered by Exa people search API + rule-based scoring

Deploy on Streamlit Cloud:
  1. Push this folder to a GitHub repo
  2. Go to share.streamlit.io → New app → point to app.py
  3. In Advanced settings → Secrets, add:
       EXA_API_KEY = "your-key-here"
       APP_PASSWORD = "your-team-password"
"""

import streamlit as st
import requests
import pandas as pd
import json
import re
from io import BytesIO
from datetime import date

# ── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Talent Sourcing Portal",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Auth gate ─────────────────────────────────────────────────────────────────

def check_password():
    """Simple shared-password gate."""
    # Try to get password from secrets (production) or fall back to session
    try:
        correct_password = st.secrets["APP_PASSWORD"]
    except Exception:
        correct_password = None  # Will use inline input below

    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        st.markdown("## 🔐 Talent Sourcing Portal")
        st.markdown("Enter the team password to continue.")
        col1, col2 = st.columns([2, 3])
        with col1:
            entered = st.text_input("Password", type="password", key="pw_input")
            if st.button("Login", type="primary"):
                if correct_password and entered == correct_password:
                    st.session_state.authenticated = True
                    st.rerun()
                elif not correct_password and entered:
                    # No secrets configured — accept any non-empty password for local dev
                    st.session_state.authenticated = True
                    st.rerun()
                else:
                    st.error("Incorrect password.")
        st.stop()

check_password()

# ── Get API key ───────────────────────────────────────────────────────────────

def get_exa_key():
    """Load Exa API key from secrets and sanitize to plain ASCII string.
    Streamlit secrets can introduce Unicode quote characters or BOM markers
    that break HTTP header encoding — strip and re-encode defensively.
    """
    try:
        raw = st.secrets["EXA_API_KEY"]
    except Exception:
        return ""
    # Coerce to plain str, strip whitespace and any invisible Unicode artifacts
    return str(raw).strip().encode("ascii", errors="ignore").decode("ascii")

# ── Helpers ───────────────────────────────────────────────────────────────────

MUST_HAVE_KEYWORDS = {
    "strategic consulting": ["strategy consultant", "management consultant", "consulting", "strategic consulting", "advanta", "mckinsey", "bcg", "bain", "deloitte", "roland berger", "oliver wyman", "accenture", "kpmg", "pwc", "strategy&", "monitor"],
    "project/workstream management": ["project manager", "workstream", "project management", "program manager", "pmo", "led team", "managed team"],
    "client stakeholder management": ["client", "stakeholder", "senior management", "board", "c-suite", "presentation", "client engagement"],
    "structured problem-solving": ["structured", "problem solving", "analysis", "analytical", "hypothesis", "framework", "structured analysis"],
    "German language": ["deutsch", "german", "deutschkenntnisse", "muttersprache"],
    "English language": ["english", "englisch", "bilingual", "fluent english"],
}

NICE_TO_HAVE_KEYWORDS = {
    "mentoring/junior development": ["mentor", "coached", "junior", "team lead", "leadership", "guided", "feedback"],
    "entrepreneurial mindset": ["entrepreneurial", "startup", "founder", "innovation", "entrepreneur"],
    "zero-defect delivery": ["zero defect", "quality", "attention to detail", "high standards", "excellence"],
    "empathetic leadership": ["empathetic", "empathy", "collaboration", "inclusive", "human-centered"],
}

COMPANY_TIER1 = ["mckinsey", "bcg", "bain", "roland berger", "oliver wyman", "strategy&", "monitor deloitte", "kearney", "arthur d little", "accenture strategy", "advanta consulting", "siemens advanta"]
COMPANY_TIER2 = ["accenture", "deloitte", "pwc", "kpmg", "ey", "capgemini", "porsche consulting", "continental", "bosch", "siemens"]

SENIORITY_WORDS = {
    "manager": 3, "director": 4, "vp": 5, "vice president": 5,
    "senior": 2, "staff": 3, "principal": 4, "lead": 3,
    "partner": 5, "associate": 1, "analyst": 0, "junior": 0, "intern": 0,
}

INDUSTRIAL_WORDS = ["industrial", "manufacturing", "automotive", "energy", "siemens", "machinery", "engineering", "aerospace", "defense", "logistics", "utilities", "technology", "digital transformation"]


def score_candidate(profile_text: str, jd_reqs: dict) -> dict:
    """Rule-based scoring against JD requirements."""
    text = (profile_text or "").lower()
    scores = {}

    # 1. Must-have skills (35 pts)
    must_haves = jd_reqs.get("must_have_skills", list(MUST_HAVE_KEYWORDS.keys()))
    mh_hits = {}
    for skill in must_haves:
        keywords = MUST_HAVE_KEYWORDS.get(skill, [skill.lower()])
        hit = any(kw in text for kw in keywords)
        mh_hits[skill] = hit
    mh_score = round(35 * sum(mh_hits.values()) / max(len(mh_hits), 1))
    scores["must_have_detail"] = mh_hits
    scores["must_have"] = mh_score

    # 2. Seniority (15 pts)
    target_seniority = jd_reqs.get("seniority_level", 2)  # default: Senior
    detected_level = 0
    for word, level in SENIORITY_WORDS.items():
        if word in text:
            detected_level = max(detected_level, level)
    diff = abs(detected_level - target_seniority)
    seniority_score = 15 if diff == 0 else (8 if diff == 1 else 0)
    scores["seniority"] = seniority_score

    # 3. Experience years (10 pts) — rough heuristic from text
    year_mentions = re.findall(r'(\d+)\s*(?:year|yr)', text)
    max_years = max([int(y) for y in year_mentions], default=0)
    min_exp = jd_reqs.get("years_exp_min", 4)
    if max_years >= min_exp:
        exp_score = 10
    elif max_years >= min_exp - 1:
        exp_score = 7
    else:
        exp_score = 3
    scores["experience"] = exp_score

    # 4. Nice-to-have (15 pts)
    nth_hits = {}
    for skill, keywords in NICE_TO_HAVE_KEYWORDS.items():
        nth_hits[skill] = any(kw in text for kw in keywords)
    nth_score = round(15 * sum(nth_hits.values()) / max(len(nth_hits), 1))
    scores["nice_to_have"] = nth_score

    # 5. Industry (10 pts)
    industries = jd_reqs.get("industries", INDUSTRIAL_WORDS)
    if any(ind.lower() in text for ind in industries):
        industry_score = 10
    elif any(w in text for w in INDUSTRIAL_WORDS):
        industry_score = 5
    else:
        industry_score = 0
    scores["industry"] = industry_score

    # 6. Company signal (10 pts)
    if any(c in text for c in COMPANY_TIER1):
        company_score = 10
    elif any(c in text for c in COMPANY_TIER2):
        company_score = 5
    else:
        company_score = 0
    scores["company"] = company_score

    # 7. Location (5 pts)
    locations = [loc.lower() for loc in jd_reqs.get("location", ["germany"])]
    if any(loc in text for loc in locations):
        location_score = 5
    elif "remote" in text:
        location_score = 3
    else:
        location_score = 0
    scores["location"] = location_score

    # Data quality penalty
    word_count = len(text.split())
    if word_count < 50:
        quality_penalty = 10
        quality = "sparse"
    elif word_count < 150:
        quality_penalty = 3
        quality = "partial"
    else:
        quality_penalty = 0
        quality = "full"
    scores["quality_penalty"] = quality_penalty
    scores["data_quality"] = quality

    total = (mh_score + seniority_score + exp_score + nth_score
             + industry_score + company_score + location_score - quality_penalty)
    scores["total"] = max(0, min(105, total))

    return scores


def grade_label(score: int) -> str:
    if score >= 75:
        return "🟢 Strong Match"
    if score >= 50:
        return "🟡 Potential"
    return "🔴 Weak Match"


def build_boolean_string(jd_text: str, refinement: str = "") -> str:
    """Generate a Google X-Ray Boolean string from JD text."""
    text = jd_text.lower()

    # Detect seniority
    seniority_terms = []
    if any(w in text for w in ["senior", "sr."]):
        seniority_terms = ['"senior"', '"sr."']
    if any(w in text for w in ["manager", "management"]):
        seniority_terms.append('"manager"')
    if any(w in text for w in ["director"]):
        seniority_terms.append('"director"')
    if not seniority_terms:
        seniority_terms = ['"senior"', '"manager"', '"lead"']

    # Apply refinements
    refinement_lower = refinement.lower()
    company_filter = ""
    exclude_filter = "-recruiter -intern -junior"

    if "mbb" in refinement_lower or "mckinsey" in refinement_lower or "bcg" in refinement_lower or "bain" in refinement_lower:
        company_filter = '("McKinsey" OR "BCG" OR "Bain")'
    elif "big4" in refinement_lower or "big 4" in refinement_lower or "deloitte" in refinement_lower:
        company_filter = '("Deloitte" OR "PwC" OR "KPMG" OR "EY")'
    elif "siemens" in refinement_lower:
        company_filter = '"Siemens"'
    else:
        company_filter = '("Siemens" OR "McKinsey" OR "BCG" OR "Bain" OR "Deloitte" OR "Accenture" OR "Roland Berger")'

    if "no automotive" in refinement_lower or "remove automotive" in refinement_lower:
        exclude_filter += " -automotive -Porsche -BMW -Daimler"

    lang_filter = ""
    if "german" in text:
        lang_filter = '("German" OR "Deutsch")'

    parts = [
        'site:linkedin.com/in/',
        '("strategy consultant" OR "management consultant" OR "strategic consultant")',
        f'({" OR ".join(seniority_terms)})',
    ]
    if lang_filter:
        parts.append(lang_filter)
    parts.append(company_filter)
    parts.append(exclude_filter)

    return " ".join(parts)


def search_exa(query: str, api_key: str, num_results: int = 20) -> list:
    """Call Exa people search API."""
    resp = requests.post(
        "https://api.exa.ai/search",
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
        json={
            "query": f"category:people {query}",
            "type": "auto",
            "num_results": num_results,
            "contents": {"highlights": {"max_characters": 3000}},
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def parse_result(r: dict) -> dict:
    """Flatten an Exa result into a flat candidate dict."""
    highlights = r.get("highlights", [])
    full_text = " ".join(highlights) if isinstance(highlights, list) else str(highlights)
    return {
        "name": r.get("author") or r.get("title", "Unknown"),
        "headline": r.get("title", ""),
        "linkedin_url": r.get("url", ""),
        "highlights": full_text[:1500],
        "published": r.get("publishedDate", ""),
    }


def build_excel_bytes(df: pd.DataFrame, jd_text: str, boolean_str: str) -> bytes:
    """Return Excel bytes for download."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import PatternFill, Font, Alignment
        from openpyxl.utils import get_column_letter

        wb = Workbook()
        ws = wb.active
        ws.title = "Candidates"

        headers = ["Rank", "Score", "Grade", "Name", "Headline", "LinkedIn URL",
                   "Must-Have Skills", "Data Quality", "Profile Highlights"]
        col_widths = [6, 7, 18, 25, 40, 30, 40, 12, 60]

        header_fill = PatternFill("solid", fgColor="1F3864")
        header_font = Font(bold=True, color="FFFFFF", size=11)
        green_fill = PatternFill("solid", fgColor="C6EFCE")
        yellow_fill = PatternFill("solid", fgColor="FFEB9C")
        red_fill = PatternFill("solid", fgColor="FFC7CE")
        wrap = Alignment(wrap_text=True, vertical="top")
        center = Alignment(horizontal="center", vertical="center")

        ws.append(headers)
        for i, (cell, width) in enumerate(zip(ws[1], col_widths), 1):
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = center
            ws.column_dimensions[get_column_letter(i)].width = width
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

        for _, row in df.iterrows():
            score = int(row.get("score", 0))
            fill = green_fill if score >= 75 else (yellow_fill if score >= 50 else red_fill)
            mh_detail = row.get("must_have_detail", {})
            mh_str = ", ".join(f"{k} {'✓' if v else '✗'}" for k, v in mh_detail.items()) if isinstance(mh_detail, dict) else ""
            ws.append([
                int(row.get("rank", 0)),
                score,
                grade_label(score),
                str(row.get("name", "")),
                str(row.get("headline", "")),
                str(row.get("linkedin_url", "")),
                mh_str,
                str(row.get("data_quality", "")),
                str(row.get("highlights", ""))[:500],
            ])
            row_idx = ws.max_row
            for cell in ws[row_idx]:
                cell.fill = fill
                cell.alignment = wrap
            ws.row_dimensions[row_idx].height = 60

        # Sheet 2: search artefacts
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
    except Exception as e:
        # Fallback to CSV bytes if openpyxl unavailable
        buf = BytesIO()
        df.to_csv(buf, index=False)
        return buf.getvalue()


# ── Session state init ────────────────────────────────────────────────────────

if "results_df" not in st.session_state:
    st.session_state.results_df = None
if "boolean_str" not in st.session_state:
    st.session_state.boolean_str = ""
if "search_query" not in st.session_state:
    st.session_state.search_query = ""
if "jd_text" not in st.session_state:
    st.session_state.jd_text = ""

# ── Sidebar ───────────────────────────────────────────────────────────────────

exa_key = get_exa_key()

with st.sidebar:
    st.markdown("## ⚙️ Settings")

    if not exa_key:
        st.error("⚠️ EXA_API_KEY not found in secrets. Ask your admin to configure it.")

    num_results = st.slider("Results per search", 5, 50, 20)

    st.divider()
    st.markdown("### 🎯 Scoring Weights")
    st.caption("Adjust what matters most for this search")
    w_must = st.slider("Must-have skills", 0, 50, 35, 5)
    w_seniority = st.slider("Seniority match", 0, 25, 15, 5)
    w_company = st.slider("Company signal", 0, 20, 10, 5)

    st.divider()
    st.markdown("### 📖 About")
    st.caption("Searches LinkedIn profiles via Exa's people index. Scoring is rule-based — add a Claude API key for AI-powered grading.")

# ── Main UI ───────────────────────────────────────────────────────────────────

st.markdown("# 🔍 Talent Sourcing Portal")
st.caption("Paste a Job Description → get ranked candidates from LinkedIn → export to Excel")

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
        seniority_opt = st.selectbox("Seniority", ["Senior / Manager", "Staff / Principal", "Director / VP", "Any"])
        industry_tags = st.multiselect(
            "Industries",
            ["industrial", "technology", "energy", "automotive", "fintech", "healthcare", "manufacturing"],
            default=["industrial", "technology", "energy"],
        )
        extra_must = st.text_input("Additional must-have keyword", placeholder="e.g. Python, MBA, fluent German")

        st.markdown("#### Refinement")
        refinement = st.text_input(
            "Refine search (natural language)",
            placeholder='e.g. "focus on MBB alumni only" or "remove automotive"',
            key="refinement_input",
        )

    if st.button("🚀 Run Search", type="primary", use_container_width=True):
        if not jd_text.strip():
            st.error("Please paste a Job Description first.")
        elif not exa_key:
            st.error("Exa API key not configured. Ask your admin to add EXA_API_KEY to Streamlit secrets.")
        else:
            st.session_state.jd_text = jd_text

            # Build JD requirements dict
            seniority_map = {
                "Senior / Manager": 2,
                "Staff / Principal": 3,
                "Director / VP": 4,
                "Any": 2,
            }
            jd_reqs = {
                "must_have_skills": list(MUST_HAVE_KEYWORDS.keys()),
                "location": [loc.strip() for loc in location_filter.split(",")],
                "seniority_level": seniority_map.get(seniority_opt, 2),
                "years_exp_min": 4,
                "industries": industry_tags or INDUSTRIAL_WORDS,
            }

            # Build query
            seniority_str = {
                "Senior / Manager": "senior manager",
                "Staff / Principal": "staff principal",
                "Director / VP": "director VP",
                "Any": "senior",
            }.get(seniority_opt, "senior")

            industry_str = " ".join(industry_tags) if industry_tags else "industrial technology"
            base_query = f"{seniority_str} strategy consultant {location_filter} {industry_str}"
            if extra_must:
                base_query += f" {extra_must}"
            if refinement:
                # Incorporate refinement into query
                if "mbb" in refinement.lower():
                    base_query += " McKinsey BCG Bain"
                elif "big4" in refinement.lower():
                    base_query += " Deloitte PwC KPMG EY"
                if "german" in refinement.lower() and "native" in refinement.lower():
                    base_query += " native German speaker"

            boolean_str = build_boolean_string(jd_text, refinement)
            st.session_state.boolean_str = boolean_str
            st.session_state.search_query = base_query

            with st.spinner(f"Searching Exa for '{base_query[:60]}...'"):
                try:
                    raw_results = search_exa(base_query, exa_key, num_results)
                    candidates = []
                    for r in raw_results:
                        c = parse_result(r)
                        scores = score_candidate(c["highlights"], jd_reqs)
                        c.update(scores)
                        c["grade"] = grade_label(c["total"])
                        c["score"] = c["total"]
                        candidates.append(c)

                    df = pd.DataFrame(candidates)
                    df = df.sort_values("score", ascending=False).reset_index(drop=True)
                    df["rank"] = df.index + 1
                    st.session_state.results_df = df
                    st.success(f"✅ Found {len(df)} candidates. Switch to the **Results** tab.")
                except requests.exceptions.RequestException as e:
                    st.error(f"Exa API error: {e}")

# ── TAB 2: Results ────────────────────────────────────────────────────────────

with tab_results:
    df = st.session_state.results_df

    if df is None:
        st.info("Run a search first to see results here.")
    else:
        # Summary metrics
        strong = (df["score"] >= 75).sum()
        potential = ((df["score"] >= 50) & (df["score"] < 75)).sum()
        weak = (df["score"] < 50).sum()

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Candidates", len(df))
        m2.metric("🟢 Strong Match", strong)
        m3.metric("🟡 Potential", potential)
        m4.metric("🔴 Weak Match", weak)

        st.divider()

        # Filter
        grade_filter = st.multiselect(
            "Filter by grade",
            ["🟢 Strong Match", "🟡 Potential", "🔴 Weak Match"],
            default=["🟢 Strong Match", "🟡 Potential"],
        )
        filtered_df = df[df["grade"].isin(grade_filter)] if grade_filter else df

        # Display table
        display_cols = ["rank", "score", "grade", "name", "headline", "data_quality", "linkedin_url"]
        available = [c for c in display_cols if c in filtered_df.columns]

        st.dataframe(
            filtered_df[available].rename(columns={
                "rank": "Rank", "score": "Score", "grade": "Grade",
                "name": "Name", "headline": "Headline",
                "data_quality": "Data Quality", "linkedin_url": "LinkedIn"
            }),
            use_container_width=True,
            height=400,
            column_config={
                "LinkedIn": st.column_config.LinkColumn("LinkedIn"),
                "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=105),
            },
            hide_index=True,
        )

        st.divider()

        # Candidate detail expanders
        st.markdown("#### 📋 Candidate Details")
        for _, row in filtered_df.head(10).iterrows():
            score = int(row.get("score", 0))
            grade = row.get("grade", "")
            with st.expander(f"{grade} **{row.get('name', 'Unknown')}** — Score: {score} | {row.get('headline', '')}"):
                c1, c2 = st.columns([2, 1])
                with c1:
                    st.markdown(f"**LinkedIn:** [{row.get('linkedin_url', '')}]({row.get('linkedin_url', '')})")
                    st.markdown("**Profile Highlights:**")
                    st.caption(str(row.get("highlights", ""))[:600])
                with c2:
                    st.markdown("**Score Breakdown:**")
                    mh_detail = row.get("must_have_detail", {})
                    if isinstance(mh_detail, dict):
                        for skill, hit in mh_detail.items():
                            st.write(f"{'✅' if hit else '❌'} {skill}")
                    st.metric("Must-Have", f"{row.get('must_have', 0)}/35")
                    st.metric("Seniority", f"{row.get('seniority', 0)}/15")
                    st.metric("Industry", f"{row.get('industry', 0)}/10")
                    st.metric("Company Signal", f"{row.get('company', 0)}/10")

        st.divider()

        # Export
        st.markdown("#### 📥 Export")
        col_a, col_b = st.columns(2)
        with col_a:
            excel_bytes = build_excel_bytes(
                filtered_df,
                st.session_state.jd_text,
                st.session_state.boolean_str,
            )
            filename = f"candidates_{date.today()}.xlsx"
            st.download_button(
                "⬇️ Download Excel",
                data=excel_bytes,
                file_name=filename,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                type="primary",
            )
        with col_b:
            csv = filtered_df[available].to_csv(index=False)
            st.download_button(
                "⬇️ Download CSV",
                data=csv,
                file_name=f"candidates_{date.today()}.csv",
                mime="text/csv",
                use_container_width=True,
            )

# ── TAB 3: Search String ──────────────────────────────────────────────────────

with tab_string:
    st.markdown("### Boolean / X-Ray Search String")
    st.caption("Edit this directly and re-run, or copy it into Google to do a manual LinkedIn X-Ray search.")

    boolean_str = st.text_area(
        "Search String (editable)",
        value=st.session_state.boolean_str or "Run a search first to generate the string.",
        height=120,
        key="editable_boolean",
    )
    if boolean_str != st.session_state.boolean_str:
        st.session_state.boolean_str = boolean_str

    st.markdown("#### Last Exa API Query")
    st.code(st.session_state.search_query or "(not yet run)", language="text")

    st.divider()
    st.markdown("#### Manual Google X-Ray")
    st.caption("Paste the Boolean string above into Google to search LinkedIn directly.")
    if st.session_state.boolean_str:
        import urllib.parse
        encoded = urllib.parse.quote(st.session_state.boolean_str)
        google_url = f"https://www.google.com/search?q={encoded}"
        st.markdown(f"[🔗 Open in Google]({google_url})")

    st.divider()
    st.markdown("#### Refinement Suggestions")
    st.markdown("""
| Intent | What to type in 'Refine search' |
|---|---|
| MBB alumni only | `focus on MBB background only` |
| Remove automotive | `remove automotive, focus on energy` |
| German native speakers | `German native speakers only` |
| More senior | `only Principal or Director level` |
| Specific city | `Munich only` or `Berlin and Hamburg` |
| Tier-1 consulting firms | `Big4 or top-tier consulting firms` |
| Use deep search | `use deep search for more results` |
    """)
