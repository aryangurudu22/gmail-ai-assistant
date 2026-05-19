from flask import Flask, render_template, session, redirect, url_for, request, jsonify
from dotenv import load_dotenv
# OAuth flow manager — handles the entire Google login process
from google_auth_oauthlib.flow import Flow
# Gmail API client builder — lets us read emails once we have access
from googleapiclient.discovery import build
# Credentials object — stores and manages access tokens
from google.oauth2.credentials import Credentials
# Handles refreshing access tokens when they expire
import google.auth.transport.requests
import os
import requests 
# Groq AI client — used to analyse each email with AI
from groq import Groq
from dateutil import parser # date parsing library to handle different email date formats
import json



# Load environment variables from .env file into memory
load_dotenv()

# Allow OAuth to work over HTTP on localhost during development only
# Remove this line when deploying to production
if os.environ.get("FLASK_ENV") != "production":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# These pull your Mumbai database credentials from the .env file
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# This is the direct API endpoint for the 'emails' table we created
TABLE_URL = f"{SUPABASE_URL}/rest/v1/emails"

# This is the direct API endpoint for the access_codes table
# Think of it as the address of our guest list in the database
ACCESS_CODES_URL = f"{SUPABASE_URL}/rest/v1/access_codes"

# These headers act as your digital keys to enter the database
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal" # Tells Supabase to just confirm 'OK' without sending back the whole data
}

# Create the Flask application instance
app = Flask(__name__)

# Load the Flask secret key from environment variables
# This key encrypts session cookies — never share it publicly
app.secret_key = os.getenv("FLASK_SECRET_KEY")

# Initialize the Groq AI client with the API key from environment variables
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

def update_memory_draft(message_id, draft_reply, gmail_compose_url):
    """
    WHAT: Updates an existing Supabase row with the draft reply and compose URL.
    WHY: These two fields are built in the dashboard route AFTER analyse_email runs.
    We save them back to Supabase so cached emails also have the draft reply stored.
    Next time the same email loads from cache, it already has the reply ready.
    """
    try:
        # PATCH updates only the specified fields — leaves everything else untouched
        url = f"{TABLE_URL}?message_id=eq.{message_id}"
        update_data = {
            "draft_reply": str(draft_reply),
            "gmail_compose_url": str(gmail_compose_url)
        }
        patch_headers = {**HEADERS, "Prefer": "return=minimal"}
        response = requests.patch(url, headers=patch_headers, json=update_data, timeout=5)
        if response.status_code in [200, 201, 204]:
            print(f"✅ DRAFT SAVED: {message_id[:20]}")
        else:
            print(f"❌ DRAFT SAVE ERROR: {response.status_code}")
    except Exception as e:
        print(f"❌ DRAFT SAVE EXCEPTION: {e}")


def verify_access_code(code):
    """
    Checks if the entered access code exists in Supabase and is active.

    WHAT: Takes the code the user typed, searches the access_codes table,
    and checks if that code exists AND has active = true.

    WHY: Codes live in Supabase so you can add, remove, or deactivate
    them from the dashboard at any time without touching this code.

    Returns True if valid and active. Returns False if not found or deactivated.
    """
    try:
        # Build the Supabase query — eq. means "equals"
        # We search for the exact code AND require active = true
        # .strip() removes any accidental spaces, .upper() makes it case-insensitive
        url = f"{ACCESS_CODES_URL}?code=eq.{code.strip().upper()}&active=eq.true"

        # Ask Supabase to search for this code
        response = requests.get(url, headers=HEADERS, timeout=5)

        # Supabase returns a list — if it has at least one item, code is valid
        data = response.json()
        if data and len(data) > 0:
            print(f"✅ ACCESS CODE VALID: {code}")
            return True
        else:
            print(f"❌ ACCESS CODE INVALID: {code}")
            return False

    except Exception as e:
        # If Supabase is unreachable — deny access to be safe, never crash
        print(f"❌ ACCESS CODE CHECK ERROR: {e}")
        return False

def save_to_memory(sender, subject, analysis_dict, user_email, message_id, raw_date, draft_reply="", gmail_compose_url=""):
    # Professional Date Parsing (Permanent Fix for NULL dates)
    try:
        clean_date = parser.parse(raw_date).isoformat()
    except Exception:
        clean_date = None 

    email_data = {
        "user_email": str(user_email), # No more hardcoding
        "message_id": str(message_id),
        "sender": str(sender),
        "subject": str(subject),
        "date": clean_date, 
        "priority": str(analysis_dict.get("priority", "Normal")),
        "spam": str(analysis_dict.get("spam", "Legitimate")),
        "summary": str(analysis_dict.get("summary", "No summary")),
        "action_required": str(analysis_dict.get("action_required", "None")),
        "response_needed": str(analysis_dict.get("response_needed", "No")),
        "gmail_link": f"https://mail.google.com/mail/u/0/#inbox/{message_id}",
        # Save draft reply and compose URL so we never re-analyse the same email
        # These are built after AI analysis and passed in from the dashboard route
        "draft_reply": str(draft_reply),
        "gmail_compose_url": str(gmail_compose_url)
    }
    
    try:
        response = requests.post(TABLE_URL, headers=HEADERS, json=email_data, timeout=10)
        if response.status_code in [200, 201]:
            print(f"✅ SECURE SYNC: {subject}")
        else:
            print(f"❌ DB ERROR: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"❌ CONNECTION ERROR: {e}")


def analyse_email(sender,subject,body,user_email, message_id, raw_date):
    """Analyse a single email using Groq Ai and return a structured analysis.
    Takes the sender, subject and body of the email as input
    Retuens a dictionary containing the priority, spam status, summary and required actions"""

    #Build the email content to string to send it to Groq ai for analysis
    #we combine the sender, subject and body so it has the full context of the email 
    email_content = f"""
    From: {sender}
    Subject: {subject}
    Body: {body}
    """
    #send the email to Groq ai for analysis
    #We will use a detailed system prompt telling the ai want we exactly want for it to return
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                #System role this sets the ai behaviour and output formatwe want 
                #this is like a job description for ai before it start working on the task
                "role": "system",
                "content": """You are an expert email analyst for busy business owners.
                
                your job is to read and analyse each email and provide a clear and structured analysys.
                
                You must respond only with a valid JSON object - no extra text, no markdown, no explainations outside the JSON.
                
                use exactly this format :
                {
                "priority": "Urgent" or "Normal" or "Low",
                "spam": "spam" or "Legitimate",
                "summary": "A clear 3 to 4 sentences summary covering what the email is about, who sent it, what they want, any important details and any deadlines mentioned",
                "action_required": "One specific clear action that the recipient needs to take, or 'No action required' if no action is needed",
                "response_needed": "Yes" or "No",
                "draft_reply": "Only generate this if response_needed is Yes. Write a professionally formatted email reply in first person as the recipient. Structure: greeting (e.g. Hi [Name],), 2 to 3 clear paragraphs addressing the email, professional sign-off (e.g. Best regards,) with a placeholder name. If response_needed is No, return an empty string here."
                }
                
                Priority rules:
                -Urgent: requires action within 24 hours, contains deadlines, payments, security alerts, or time sensitive requests.
                -Normal: requires action but no immediate deadline.
                -Low: newsletters, promotions, or informational emails that do not require action.
                
                Spam rules:
                -Spam: unsolicited emails, phishing attempts, or anything that looks suspicious or potentially harmful.
                -Legitimate: from known senders, expected communications, prefessional correspondance, or anything that does not look like spam."""

            },
            {
                #user role - this is the actual email content we want the ai to analyse
                "role": "user",
                "content": f"please analyse this email:\n\n{email_content}"
            }
        ],
        #temperature controls how creative or random the ai responses are - we want a low temperature for consistent structured output
        #0.5 is a good balance for structured tasks like this - it allows some variation in the analysis while still keeping it focused and on point

        temperature = 0.3,
        max_tokens = 800

    )

    #Extract the ai response text
    ai_response =  response.choices[0].message.content

    

    #parse the ai text as json response into the python dictionary 
    #the ai is instructed to only respond in json so we can parse itt directy

    import json 
    try:
        #try to parse the response as json
        analysis = json.loads(ai_response)

        # --- TRIGGER MEMORY SYNC ---
        # If the analysis was successful, save it to the database
        # We now pass the whole dictionary instead of the raw text
        # draft_reply and gmail_compose_url are built after this function returns
        # They are saved via update_memory_draft in the dashboard route
        save_to_memory(sender, subject, analysis, user_email, message_id, raw_date)

    except json.JSONDecodeError:
        #if parsing fails at any instance return a safe default analysis
        #this ensures that even if one email fails to be analysed the app doesnt crash
        analysis = {
            "priority": "Normal",
            "spam": "Legitimate",
            "summary": "Could not analyse this email.",
            "action_required": "Please review this email manually.",
            "response_needed": "UNKNOWN"
        }
    return analysis


# Home page route — goes straight to Google login
# Access code check happens AFTER login, before dashboard
@app.route("/")
def index():
    """
    WHAT: Landing page — the first thing a user sees when visiting the app.
    WHY: Shows the branded Connect with Gmail page with features and CTA.
    This was the original index.html we built — restoring it properly.

    If already logged in and access granted, skip straight to dashboard.
    """
    # Already fully authenticated — send straight to dashboard
    if session.get("credentials") and session.get("access_granted"):
        return redirect(url_for("dashboard"))

    # Show the branded landing page with Connect Gmail button
    return render_template("index.html")


# Access code page route — shown after Google login
# GET request — just displays the enter_code.html page
@app.route("/enter-code")
def verify_code_page():
    """
    WHAT: Shows the access code entry page after Google login.
    WHY: Client has proved who they are via Google.
    Now we check they are a paying client before showing their emails.

    If they already have a valid code this session, skip to dashboard.
    """
    # Already has a valid code this session — skip to dashboard
    if session.get("access_granted"):
        return redirect(url_for("dashboard"))

    # Must be logged in to reach this page — if not, back to login
    if "credentials" not in session:
        return redirect(url_for("login"))

    # Show the access code entry page
    return render_template("enter_code.html")


# Verify code route — receives and checks the code the user typed
@app.route("/verify-code", methods=["POST"])
def verify_code():
    """
    WHAT: Handles the form submission from the access code page.
    WHY: When user types their code and clicks Continue, this route
    receives it, checks Supabase, and either lets them through or
    shows an error.

    methods=["POST"] means this only accepts form submissions —
    nobody can reach it by typing the URL directly in the browser.
    """
    # Guard — must be logged in via Google before submitting access code
    # If someone posts directly to /verify-code without logging in, send them to login
    if "credentials" not in session:
        return redirect(url_for("login"))

    # Get the code the user typed — strip removes spaces, upper makes it case-insensitive
    entered_code = request.form.get("access_code", "").strip().upper()

    # Empty submission — send back with error
    if not entered_code:
        return render_template("enter_code.html", error="Please enter your access code.")

    # Check the code against our Supabase access_codes table
    if verify_access_code(entered_code):
        # Valid code — mark this session as approved
        # This flag means they will not be asked for the code again this session
        session["access_granted"] = True
        session.modified = True  # Tell Flask the session changed so it saves it

        # Send them to dashboard — they passed Gate 2
        return redirect(url_for("dashboard"))
    else:
        # Invalid code — send back to code page with a clear error message
        return render_template("enter_code.html", error="Invalid access code. Please check your code and try again.")

# Login route — builds Google OAuth URL and redirects user to Google login page
@app.route("/login")
def login():
    # Create OAuth flow using credentials from client_secret.json
    google_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    if not google_secret:
        return "Server misconfiguration: GOOGLE_CLIENT_SECRET env var is missing.", 500
    client_config = json.loads(google_secret)
    flow = Flow.from_client_config(
        client_config,
        # Scopes define what permissions we are requesting from Google
        # gmail.readonly means read only — we cannot send or delete emails
        scopes=["https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/userinfo.email",
                "openid"],
        # Redirect URI is where Google sends the user after they log in
        redirect_uri="https://gmail-ai-assistant.up.railway.app/callback"
    )

    # Generate the Google login URL and a state token for security
    authorization_url, state = flow.authorization_url(
        # Offline access means we get a refresh token to stay connected
        # even when the user is not actively using the app
        access_type="offline",
        # Do not ask for permissions the user has already granted
        include_granted_scopes="true",
        prompt="consent"  # Forces Google to always return a refresh_token
    )

    # Save state in session as a backup — we also read it from URL in callback
    session["state"] = state
    session.modified = True

    # Send user to Google login page
    return redirect(authorization_url)

# Callback route — Google redirects here after user logs in and grants permission
@app.route("/callback")
def callback():
    # Prevent "Scope Change" warnings from crashing the app
    os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'

    # If Google returned an error (e.g. user denied access), redirect home
    if request.args.get("error"):
        return redirect(url_for("index"))

    state = request.args.get("state")

    google_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    if not google_secret:
        return "Server misconfiguration: GOOGLE_CLIENT_SECRET env var is missing.", 500

    client_config = json.loads(google_secret)
    flow = Flow.from_client_config(
        client_config,
        scopes=["https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/userinfo.email",
                "openid"],
        redirect_uri="https://gmail-ai-assistant.up.railway.app/callback",
        state=state
    )

    # Exchange the authorisation code for real tokens
    try:
        flow.fetch_token(authorization_response=request.url)
    except Exception as e:
        print(f"❌ Token fetch error: {e}")
        return redirect(url_for("login"))

    # Get the credentials object
    credentials = flow.credentials

    # Guard: if refresh_token is missing, revoke and force clean re-auth
    if not credentials.refresh_token:
        print("⚠️ No refresh_token received — redirecting to force consent")
        return redirect(url_for("login"))

    # Get user email directly from the token's id_token claims (no extra API call needed)
    # Fallback to userinfo API if id_token not available
    user_email = None
    try:
        import google.auth.transport.requests as google_requests
        from google.oauth2 import id_token as google_id_token
        request_session = google_requests.Request()
        id_info = google_id_token.verify_oauth2_token(
            credentials.id_token,
            request_session,
            credentials.client_id
        )
        user_email = id_info.get("email")
    except Exception:
        pass

    # Fallback: use userinfo API if id_token parsing failed
    if not user_email:
        try:
            user_info_service = build("oauth2", "v2", credentials=credentials)
            user_info = user_info_service.userinfo().get().execute()
            user_email = user_info.get("email", "unknown_user")
        except Exception as e:
            print(f"⚠️ Could not fetch user email: {e}")
            user_email = "unknown_user"

    # Store credentials in session
    session["credentials"] = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": list(credentials.scopes) if credentials.scopes else []
    }
    session['user'] = {'email': user_email}
    session.modified = True

    # After successful Google login, redirect to access code entry
    # The client must enter their paid access code before seeing any data
    return redirect(url_for("verify_code_page"))


def get_existing_memory(message_id):
    """
    Checks Supabase to see if this email was already analyzed.
    """
    # Search the table for this specific Gmail Message ID
    url = f"{TABLE_URL}?message_id=eq.{message_id}"
    try:
        response = requests.get(url, headers=HEADERS, timeout=5)
        data = response.json()
        if data and len(data) > 0:
            return data[0]  # Found it! Return the saved data
    except Exception as e:
        print(f"⚠️ Database Search Error: {e}")
    return None # Not found


# Dashboard route — shows the AI analysis of the user's emails
@app.route("/dashboard")
def dashboard():
    # Pull the actual logged-in user email from the session
    user_info = session.get('user', {})
    actual_email = user_info.get('email', 'unknown_user')

    # Security guard — if access code not verified, send back to start
    # This prevents bypassing the code page by typing /dashboard directly
    if not session.get("access_granted"):
        return redirect(url_for("index"))

    # Route guard — if user is not logged in send them back to home page
    if "credentials" not in session:
        return redirect(url_for("index"))

    # Rebuild the Credentials object from the dictionary stored in session
    # We need a proper Credentials object to make Gmail API calls
    creds_data = session["credentials"]

    # Force re-login if gmail scope is missing (e.g. old session before scope fix)
    required_scope = "https://www.googleapis.com/auth/gmail.readonly"
    stored_scopes = creds_data.get("scopes", [])
    if required_scope not in stored_scopes:
        session.clear()
        return redirect(url_for("login"))

    credentials = Credentials(
        token=creds_data["token"],
        refresh_token=creds_data["refresh_token"],
        token_uri=creds_data["token_uri"],
        client_id=creds_data["client_id"],
        client_secret=creds_data["client_secret"],
        scopes=creds_data["scopes"]
    )

    # Refresh token if expired
    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(google.auth.transport.requests.Request())
            session["credentials"]["token"] = credentials.token
            session.modified = True
        except Exception as e:
            print(f"❌ Token refresh failed: {e}")
            session.clear()
            return redirect(url_for("login"))

    # Build the Gmail API client using the credentials
    # "v1" means version 1 of the Gmail API
    gmail_service = build("gmail", "v1", credentials=credentials)

    # Step 1 — Get the list of unread email IDs from Gmail
    # This only gives us the ID of each email, not the full content yet
    results = gmail_service.users().messages().list(
        userId="me",
        # category:primary filters to primary inbox only — excludes promotions, social, updates tabs
        # newer_than:7d fetches only emails from the last 7 days
        # is:unread ensures we only show unread emails the client has not read yet
        maxResults=10,  # Capped at 10 to prevent Groq rate limit on fresh load
        q="is:unread category:primary newer_than:7d"
    ).execute()

    # Get the list of message ID objects — each one looks like {"id": "abc123"}
    # If no unread emails exist, return an empty list instead of crashing
    message_list = results.get("messages", [])

    # Step 2 — For each email ID, fetch the full email content
    # Think of this like opening each envelope one by one to read what is inside
    emails = []
    for msg in message_list:

        # Fetch the complete email using its unique ID
        # format="full" means give us everything — headers, body, all parts
        message = gmail_service.users().messages().get(
            userId="me",
            id=msg["id"],
            format="full"
        ).execute()

        # Step 3 — Extract the email headers
        # Headers are the metadata on the outside of the envelope
        # They contain subject, sender, date — but not the actual message body
        payload = message["payload"]
        headers = payload["headers"]

        # Find the Subject header — if none exists use "No Subject" as default
        subject = next(
            (h["value"] for h in headers if h["name"] == "Subject"),
            "No Subject"
        )

        # Find the From header — who sent this email
        sender = next(
            (h["value"] for h in headers if h["name"] == "From"),
            "Unknown Sender"
        )

        # Find the Date header — when this email was sent
        date = next(
            (h["value"] for h in headers if h["name"] == "Date"),
            "Unknown Date"
        )

        
        # ============================================================
        # STEP 4 — EXTRACT EMAIL BODY WITH FULL EDGE CASE HANDLING
        # This section handles every possible email format Gmail returns
        # Plain text, HTML, nested multipart, quoted-printable, and more
        # ============================================================
        import base64
        import re
        import quopri
        import time

        def safe_decode(data, encoding="utf-8"):
            """
            Safely convert raw bytes into readable text.
            Why we need this: email bodies arrive as raw bytes, not text.
            We try UTF-8 first — the modern standard covering all languages.
            If that fails we fall back to latin-1 which accepts every possible byte value.
            This prevents the entire dashboard from crashing on one badly encoded email.
            """
            if isinstance(data, str):
                # Already a string — nothing to decode
                return data
            try:
                return data.decode(encoding)
            except (UnicodeDecodeError, AttributeError):
                try:
                    # latin-1 is the universal fallback — accepts all 256 byte values
                    return data.decode("latin-1")
                except:
                    # If everything fails return empty string — never crash
                    return ""

        def decode_base64_body(data):
            """
            Decode Gmail's base64 encoded email body back to readable text.
            Why base64: email was designed for plain text — binary data needs encoding.
            Gmail uses URL-safe base64 which swaps + for - and / for _
            We also add padding if missing — base64 strings must be divisible by 4.
            """
            try:
                # Add = padding if needed to make length divisible by 4
                padded = data + "=" * (4 - len(data) % 4)
                decoded_bytes = base64.urlsafe_b64decode(padded)
                return safe_decode(decoded_bytes)
            except Exception:
                # Return empty string on any decode failure — never crash
                return ""

        def decode_quoted_printable(data):
            """
            Decode quoted-printable encoded content.
            Some older email clients use this instead of base64.
            Example: the character = followed by E2=80=99 becomes a right quote mark.
            quopri is a built-in Python library specifically for this encoding.
            """
            try:
                return safe_decode(quopri.decodestring(data.encode()))
            except Exception:
                return ""

        def strip_html_tags(html):
            """
            Convert HTML email content into clean readable plain text.
            Why we need this: HTML emails contain tags like div, p, span, table
            that are meaningless to the AI — we want only the actual words.
            We also remove script and style blocks entirely — pure noise for AI analysis.
            Finally we decode HTML entities like &amp; back to their real characters.
            """
            if not html:
                return ""

            # Replace block-level HTML elements with newlines
            # This preserves paragraph structure in the cleaned text
            html = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
            html = re.sub(r'<p[^>]*>', '\n', html, flags=re.IGNORECASE)
            html = re.sub(r'</p>', '\n', html, flags=re.IGNORECASE)
            html = re.sub(r'<tr[^>]*>', '\n', html, flags=re.IGNORECASE)
            html = re.sub(r'<li[^>]*>', '\n• ', html, flags=re.IGNORECASE)

            # Remove script blocks entirely — JavaScript code is useless for AI
            html = re.sub(r'<script[^>]*>.*?</script>', '', html,
                         flags=re.DOTALL | re.IGNORECASE)

            # Remove style blocks entirely — CSS is useless for AI
            html = re.sub(r'<style[^>]*>.*?</style>', '', html,
                         flags=re.DOTALL | re.IGNORECASE)

            # Remove all remaining HTML tags — anything between < and >
            html = re.sub(r'<[^>]+>', '', html)

            # Decode common HTML entities back to readable characters
            html = html.replace('&amp;', '&')
            html = html.replace('&lt;', '<')
            html = html.replace('&gt;', '>')
            html = html.replace('&nbsp;', ' ')
            html = html.replace('&quot;', '"')
            html = html.replace('&#39;', "'")
            html = html.replace('&apos;', "'")
            html = html.replace('&hellip;', '...')
            html = html.replace('&rsquo;', "'")
            html = html.replace('&lsquo;', "'")
            html = html.replace('&rdquo;', '"')
            html = html.replace('&ldquo;', '"')

            # Clean up excessive whitespace while keeping paragraph breaks
            html = re.sub(r'\n{3,}', '\n\n', html)
            html = re.sub(r'[ \t]+', ' ', html)

            return html.strip()

        def extract_part_body(part):
            """
            Extract and decode text content from a single email part.
            Detects whether the part uses base64 or quoted-printable encoding
            by reading the Content-Transfer-Encoding header.
            Returns a tuple of (decoded_text, mime_type).
            """
            mime_type = part.get("mimeType", "")
            body_data = part.get("body", {})
            data = body_data.get("data", "")

            # No data in this part — return empty
            if not data:
                return "", mime_type

            # Check Content-Transfer-Encoding header to know how to decode
            headers = part.get("headers", [])
            content_transfer = next(
                (h["value"].lower() for h in headers
                 if h["name"].lower() == "content-transfer-encoding"),
                "base64"  # Default to base64 if header is missing
            )

            # Decode using the correct method based on encoding type
            if "quoted-printable" in content_transfer:
                text = decode_quoted_printable(data)
            else:
                # Base64 is the default — Gmail uses this for almost all content
                text = decode_base64_body(data)

            return text, mime_type

        def extract_body_recursive(payload, depth=0):
            """
            Recursively search through all email parts to find readable body text.
            Why recursive: emails can be nested many levels deep.
            A multipart/mixed email can contain a multipart/alternative part
            which contains both text/plain and text/html versions.
            We search all levels and collect the best available text.
            Depth limit of 10 prevents infinite loops on malformed emails.
            Priority: plain text first, HTML second — plain is cleaner for AI.
            Returns a tuple of (plain_text, html_text).
            """
            # Safety limit — stop recursing at depth 10 to prevent infinite loops
            if depth > 10:
                return "", ""

            plain_text = ""
            html_text = ""
            mime_type = payload.get("mimeType", "")

            # Case 1 — This payload has sub-parts (multipart email)
            # Recurse into each part looking for text content
            if "parts" in payload:
                for part in payload["parts"]:
                    sub_plain, sub_html = extract_body_recursive(part, depth + 1)
                    # Keep the first plain text found — do not overwrite with later parts
                    if sub_plain and not plain_text:
                        plain_text = sub_plain
                    # Keep the first HTML found
                    if sub_html and not html_text:
                        html_text = sub_html

            # Case 2 — This payload has a direct body with data
            elif "body" in payload and payload["body"].get("data"):
                text, mime = extract_part_body(payload)
                if mime == "text/plain":
                    plain_text = text
                elif mime == "text/html":
                    html_text = text

            return plain_text, html_text

        # Step 4 — Run the body extraction on the full email payload
        # This calls our recursive function to dig through all email parts
        # and return the best available plain text and HTML text
        plain_body, html_body = extract_body_recursive(payload)  # type: ignore

        # Choose the best available body content in priority order
        # Plain text is cleanest for AI — HTML has too many tags
        if plain_body.strip():
            # Best case — clean plain text found, use it directly
            body = plain_body.strip()
        elif html_body.strip():
            # Second choice — HTML found, strip tags to get clean text
            body = strip_html_tags(html_body)
        else:
            # Last resort — use Gmail's auto-generated snippet
            # Gmail creates a ~200 character preview for every email
            # Not as detailed as the full body but always available
            body = message.get("snippet", "No content available")

        # Final cleanup — collapse 3 or more blank lines into 2
        body = re.sub(r'\n{3,}', '\n\n', body).strip()

        # Step 5 — Send the email content to Groq AI for analysis
        # This calls our analyse_email function which returns a structured dictionary
        # containing the priority, spam status, summary and required actions for this email
        # Check if we already have this email in Supabase to save tokens
        existing_data = get_existing_memory(msg['id'])

        # Initialise both variables — they get set either from cache or from fresh analysis below
        draft_reply = ""
        gmail_compose_url = ""

        if existing_data:
            print(f"💾 MEMORY HIT: {subject}")
            analysis = existing_data
            # Read draft_reply and gmail_compose_url from cached Supabase data
            draft_reply = existing_data.get("draft_reply", "")
            gmail_compose_url = existing_data.get("gmail_compose_url", "")

            # If cached email has no draft reply but is Urgent or Normal
            # it was saved before this feature existed — re-analyse just for the draft
            cached_response_needed = existing_data.get("response_needed", "NO").upper()
            if not draft_reply and cached_response_needed == "YES":
                print(f"🔄 REBUILDING DRAFT: {subject}")
                try:
                    import time
                    time.sleep(3)  # Prevent Groq rate limit when rebuilding multiple drafts

                    # Call Groq directly to get only the draft reply
                    # We do NOT call analyse_email here because that would
                    # try to INSERT a new Supabase row and cause a duplicate key error
                    draft_prompt = f"""You are an email reply writer.
Write a professionally formatted email reply for the following email.
Structure: greeting, 2-3 paragraph body, professional sign-off.
Return ONLY the reply text — no JSON, no explanation.

From: {sender}
Subject: {subject}
Body: {body[:1250]}"""

                    draft_response = groq_client.chat.completions.create(
                        model="llama-3.3-70b-versatile",
                        messages=[{"role": "user", "content": draft_prompt}],
                        temperature=0.3,
                        max_tokens=500
                    )
                    draft_reply = draft_response.choices[0].message.content.strip()
                    analysis = existing_data  # Keep original analysis — only update draft
                except Exception as e:
                    print(f"⚠️ Draft rebuild failed: {e}")
                    draft_reply = ""
        else:
            try:
                import time
                time.sleep(2)  # 2 second gap between each fresh Groq call — prevents rate limit
                analysis = analyse_email(sender, subject, body[:1250], actual_email, msg['id'], date)
            except Exception as e:
                print(f"⚠️ Groq Error: Skipping {subject} — {e}")
                continue
        
        # Guard: skip if analysis is somehow None
        if not analysis:
            continue

        # Step 6 — Build draft reply and Gmail compose URL
        import re as re_module
        import urllib.parse

        # Extract sender email address for the Gmail compose URL
        # Sender field looks like "John Smith <john@example.com>" — we need just the email
        sender_email_match = re_module.search(r'<(.+?)>', sender)
        sender_email = sender_email_match.group(1) if sender_email_match else sender

        # Get priority and response_needed from analysis
        email_priority = analysis.get("priority", "Normal")
        response_needed = analysis.get("response_needed", "NO").upper()

        # IMPORTANT: Only overwrite draft_reply and gmail_compose_url from analysis
        # if they were NOT already set by the cache block above.
        # The cache block sets draft_reply directly from Groq or from Supabase.
        # We must not overwrite those values here.
        if not draft_reply:
            # Fresh analysis — get draft_reply from Groq response
            draft_reply = analysis.get("draft_reply", "")

        if not gmail_compose_url:
            # Build compose URL only when response is needed AND draft exists
            # response_needed is the single source of truth — cleaner and lighter
            if response_needed == "YES" and draft_reply:
                gmail_compose_url = (
                    f"https://mail.google.com/mail/?view=cm"
                    f"&to={urllib.parse.quote(sender_email)}"
                    f"&su={urllib.parse.quote('Re: ' + subject)}"
                    f"&body={urllib.parse.quote(draft_reply)}"
                )
            else:
                gmail_compose_url = ""

        # Save draft_reply and gmail_compose_url to Supabase permanently
        # So next load they come straight from cache — no rebuild needed
        if draft_reply or gmail_compose_url:
            update_memory_draft(msg['id'], draft_reply, gmail_compose_url)

        emails.append({
            "subject": subject,
            "sender": sender,
            "date": date,
            "body": body[:1250],
            "gmail_link": f"https://mail.google.com/mail/u/0/#inbox/{msg['id']}",  # Direct link to this exact email in Gmail

            # AI analysis results
            "priority": analysis.get("priority", "Normal"),
            "spam": analysis.get("spam", "Legitimate"),
            "summary": analysis.get("summary", "No summary available"),
            "action_required": analysis.get("action_required", "No action required"),
            "response_needed": response_needed,

            # Draft reply text — populated for Urgent and Normal emails
            "draft_reply": draft_reply,

            # Gmail compose URL — pre-fills To, Subject, Body in Gmail compose window
            # Empty string for Low priority — dashboard hides button in that case
            "gmail_compose_url": gmail_compose_url
        })

    # Sort emails by priority — Urgent first, then Normal, then Low
    # This runs AFTER the loop finishes — once all emails are collected
    # We define a custom order using a dictionary
    priority_order = {"Urgent": 0, "Normal": 1, "Low": 2}

    # Sort the emails list using the priority order
    # Emails with unknown priority go to the end with default value 99
    emails.sort(key=lambda x: priority_order.get(x["priority"], 99))

    #Now we pass them through the dashboard to display the counts aswell
    #create three empty buckets for each priority level starting at zero to hold the counts
    urgent_count = 0
    normal_count = 0
    low_count = 0

    #loop through the emaails in our final sorted list and count how many emails fall into each priority level
    for email in emails:
        if email["priority"] == "Urgent":
            urgent_count += 1
        elif email["priority"] == "Normal":
            normal_count += 1
        elif email["priority"] == "Low":
            low_count += 1


    # Pass the complete list of emails to the dashboard template to display
    return render_template("dashboard.html", emails=emails, urgent_count=urgent_count, normal_count=normal_count, low_count=low_count)

# Sync route — clears Supabase email cache for the current user
# so the next dashboard load re-analyses all emails fresh
@app.route("/sync")
def sync():
    """
    WHAT: Deletes all cached emails for this user from Supabase.
    WHY: When the client clicks Sync, they want to see their latest emails
    re-analysed fresh — not the cached version from the last visit.
    After clearing the cache, we redirect to dashboard which re-analyses everything.
    """
    # Must be logged in to sync
    if not session.get("access_granted") or "credentials" not in session:
        return redirect(url_for("index"))

    # Get the logged in user's email so we only delete their emails
    user_info = session.get('user', {})
    actual_email = user_info.get('email', '')

    if actual_email:
        try:
            # Delete only this user's cached emails — not other users' data
            url = f"{TABLE_URL}?user_email=eq.{actual_email}"
            response = requests.delete(url, headers=HEADERS, timeout=10)
            if response.status_code in [200, 204]:
                print(f"✅ SYNC: Cleared cache for {actual_email}")
            else:
                print(f"⚠️ SYNC WARNING: {response.status_code}")
        except Exception as e:
            print(f"⚠️ SYNC ERROR: {e}")

    # Redirect to dashboard — fresh analysis will run automatically
    return redirect(url_for("dashboard"))


# Logout route — clears the session and redirects to home page
@app.route("/logout")
def logout():
    # Remove all session data — credentials and access_granted both cleared
    session.clear()
    # Send user back to landing page — the branded Connect with Gmail page
    return redirect(url_for("index"))



# Run the app only when this file is executed directly — not when imported
if __name__ == "__main__":
    # --- AUTOMATIC CONNECTION TEST ---
    print("\nAttempting to connect to Cloud Brain...")
    try:
        # We perform a tiny "ping" by asking for 1 ID from your table
        test_response = requests.get(TABLE_URL, headers=HEADERS, params={"select": "id", "limit": 1})
        
        if test_response.status_code == 200:
            print("✅ SUCCESS: Your script is connected to Supabase!")
        else:
            print(f"❌ DATABASE ERROR: {test_response.status_code} - {test_response.text}")
            print("Check your .env keys and table name.")
            
    except Exception as e:
        print(f"❌ CONNECTION FAILED: {e}")
    # Start the Flask development server with debug mode enabled
    # debug=True auto-restarts on code changes and shows detailed errors
    app.run(debug=True)