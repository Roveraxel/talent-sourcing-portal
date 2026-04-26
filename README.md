# Talent Sourcing Portal

A password-protected web app for sourcing and scoring candidates using Exa's people search API.

## Deploy in 3 steps (free, ~5 minutes)

### 1. Push to GitHub
Create a new GitHub repo and push this folder to it.

```bash
git init
git add .
git commit -m "Talent sourcing portal"
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

### 2. Deploy on Streamlit Cloud
1. Go to [share.streamlit.io](https://share.streamlit.io)
2. Sign in with GitHub
3. Click **New app** → select your repo → `app.py`
4. Click **Advanced settings** → **Secrets** and paste:

```toml
EXA_API_KEY = "b4b7de40-a1cb-4d14-ad8e-9d41233dc9bc"
APP_PASSWORD = "choose-a-team-password"
```

5. Click **Deploy** — you'll get a URL like `https://your-app.streamlit.app`

### 3. Share with your team
Send the URL + password to your colleagues. They don't need a GitHub or Streamlit account.

---

## Run locally

```bash
pip install -r requirements.txt

# Set up secrets
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# Edit secrets.toml with your actual keys

streamlit run app.py
```

---

## Features

- 🔐 Password gate — shared team password, set in Streamlit secrets
- 📋 JD input — paste any job description
- 🎯 Quick filters — location, seniority, industry, extra keywords
- 🔧 Refinement — natural language refinement ("focus on MBB", "remove automotive")
- 📊 Scored results table — rule-based scoring against JD requirements
- 📋 Candidate detail view — profile highlights + score breakdown per dimension
- ⬇️ Export — one-click Excel or CSV download
- 🔧 Editable Boolean string — view, edit, and copy the X-Ray search string

## Upgrading to AI-powered scoring

Add a Claude API key to secrets.toml:
```toml
CLAUDE_API_KEY = "sk-ant-..."
```
Then swap the `score_candidate()` function in `app.py` to call the Anthropic API with the JD requirements + profile text. This gives you the full weighted rubric scoring from the Cowork talent-sourcer skill.
