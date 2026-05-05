# Gmail AI Assistant

An AI-powered email analysis tool that connects to your Gmail account, reads your unread emails, and automatically generates a priority-sorted dashboard with full AI analysis for each email.

---

## Live Demo
Coming soon — deployment in progress.

---

## What it does

- Connects securely to Gmail using Google OAuth 2.0
- Reads unread emails automatically from your inbox
- Uses Groq AI to analyse each email and extract:
  - Priority level (Urgent / Normal / Low)
  - Spam detection (Legitimate / Suspicious)
  - 3-4 sentence summary of the email content
  - Exact action required
  - Whether a response is needed (Yes / No)
- Displays everything on a clean dark dashboard
- Emails sorted by priority — Urgent always appears first
- Handles all email formats — plain text, HTML, multipart, quoted-printable

---

## Screenshot
Coming soon.

---

## Built with

- Python 3
- Flask — web framework
- Groq API (llama-3.3-70b-versatile) — AI analysis engine
- Gmail API — email access
- Google OAuth 2.0 — secure login
- HTML / CSS — dashboard frontend

---

## Installation

1. Clone this repository:
   ```
   git clone https://github.com/aryangurudu22/gmail-ai-assistant
   cd gmail-ai-assistant
   ```

2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Add your Google OAuth credentials file as `client_secret.json` in the root folder.
   Get this from Google Cloud Console → APIs & Services → Credentials.

4. Create a `.env` file in the root folder with your credentials:
   ```
   GROQ_API_KEY=your_groq_key_here
   FLASK_SECRET_KEY=your_secret_key_here
   ```

5. Run the app:
   ```
   python app.py
   ```

6. Open your browser at `http://localhost:5000`

7. Click Login, sign in with your Google account, and your dashboard loads automatically.

---

## Project structure

```
gmail-ai-assistant/
├── app.py                  — main Flask application (all logic lives here)
├── requirements.txt        — exact library versions
├── .env                    — secret keys (never pushed to GitHub)
├── client_secret.json      — Google OAuth credentials (never pushed to GitHub)
├── .gitignore              — excludes .env and client_secret.json from Git
├── README.md               — this file
├── templates/
│   ├── index.html          — landing page with login button
│   └── dashboard.html      — email analysis dashboard
└── static/
    └── style.css           — all styling for dashboard
```

---

## How it works

1. User visits the app and clicks Login
2. Google OAuth opens — user grants Gmail read permission
3. App fetches the 10 most recent unread emails from Gmail
4. Each email is sent to Groq AI for analysis
5. AI returns priority, spam status, summary, action required, and response needed
6. Dashboard displays all emails sorted by priority — Urgent first

---

## Important notes for developers

- `google-auth-oauthlib` must stay at version `0.8.0` — newer versions enable PKCE by default and break the OAuth flow
- Groq AI temperature is set to `0.3` for consistent analysis results
- Email body is capped at `2500` characters before sending to AI to avoid rate limits
- Maximum `10` emails fetched per dashboard load
- `client_secret.json` and `.env` are both gitignored — never push these to GitHub

---

## Author

Aryan Reddy — AI Automation Specialist

Building AI tools that save business owners hours every week.

LinkedIn: linkedin.com/in/aryanreddy22
Fiverr: fiverr.com/users/aryanreddy22
GitHub: github.com/aryangurudu22