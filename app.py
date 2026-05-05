from flask import Flask, render_template, session, redirect, url_for, request
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
# Groq AI client — used to analyse each email with AI
from groq import Groq


# Load environment variables from .env file into memory
load_dotenv()

# Allow OAuth to work over HTTP on localhost during development only
# Remove this line when deploying to production
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# Create the Flask application instance
app = Flask(__name__)

# Load the Flask secret key from environment variables
# This key encrypts session cookies — never share it publicly
app.secret_key = os.getenv("FLASK_SECRET_KEY")

# Initialize the Groq AI client with the API key from environment variables
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

def analyse_email(sender,subject,body):
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
                "summary": "A clear 3 to 4 sentences summary covering the what that email is about, who sent it, what they want, any important details and any dedalines mentioned",
                "action_required": "One Specific clear Action that the recipient needs to take , or 'No action required' if no action is needed",
                "response_needed": "Yes" or "No"
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


# Home page route — the first page a user sees when they visit the app
@app.route("/")
def index():
    # Find index.html in the templates folder and send it to the user's browser
    return render_template("index.html")

# Login route — builds Google OAuth URL and redirects user to Google login page
@app.route("/login")
def login():
    # Create OAuth flow using credentials from client_secret.json
    flow = Flow.from_client_secrets_file(
        "client_secret.json",
        # Scopes define what permissions we are requesting from Google
        # gmail.readonly means read only — we cannot send or delete emails
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        # Redirect URI is where Google sends the user after they log in
        redirect_uri="http://localhost:5000/callback"
    )

    # Generate the Google login URL and a state token for security
    authorization_url, state = flow.authorization_url(
        # Offline access means we get a refresh token to stay connected
        # even when the user is not actively using the app
        access_type="offline",
        # Do not ask for permissions the user has already granted
        include_granted_scopes="true"
    )

    # Save state in session as a backup — we also read it from URL in callback
    session["state"] = state
    session.modified = True

    # Send user to Google login page
    return redirect(authorization_url)

# Callback route — Google redirects here after user logs in and grants permission
@app.route("/callback")
def callback():
    # Read state directly from the URL Google sends back
    # This is more reliable than reading from session on Python 3.14
    state = request.args.get("state")

    # Recreate the OAuth flow with same settings as login route
    # We pass the state so Google can verify this is a legitimate callback
    flow = Flow.from_client_secrets_file(
        "client_secret.json",
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        redirect_uri="http://localhost:5000/callback",
        state=state
    )

    # Exchange the authorisation code Google sent for a real access token
    # This is the most important step — without this we cannot read emails
    flow.fetch_token(
    authorization_response=request.url,
    client_secret=flow.client_config["client_secret"]
)

    # Get the credentials object containing access token and refresh token
    credentials = flow.credentials

    # Store credentials in session as a dictionary so user stays logged in
    # We store as dictionary because the Credentials object cannot be serialised directly
    session["credentials"] = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes
    }
    # Mark session as modified so Flask saves the changes
    session.modified = True

    # Redirect user to dashboard now that they are logged in
    return redirect(url_for("dashboard"))

# Dashboard route — shows the AI analysis of the user's emails
@app.route("/dashboard")
def dashboard():
    # Route guard — if user is not logged in send them back to home page
    if "credentials" not in session:
        return redirect(url_for("index"))

    # Rebuild the Credentials object from the dictionary stored in session
    # We need a proper Credentials object to make Gmail API calls
    credentials = Credentials(
        token=session["credentials"]["token"],
        refresh_token=session["credentials"]["refresh_token"],
        token_uri=session["credentials"]["token_uri"],
        client_id=session["credentials"]["client_id"],
        client_secret=session["credentials"]["client_secret"],
        scopes=session["credentials"]["scopes"]
    )

    # Build the Gmail API client using the credentials
    # "v1" means version 1 of the Gmail API
    gmail_service = build("gmail", "v1", credentials=credentials)

    # Step 1 — Get the list of unread email IDs from Gmail
    # This only gives us the ID of each email, not the full content yet
    results = gmail_service.users().messages().list(
        userId="me",
        labelIds=["UNREAD"],  # Only fetch unread emails
        maxResults=10,  # Limit to 10 emails for now
        q = "in:inbox" # Only search for emails in the inbox, not sent or archived emails
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
        time.sleep(2)# Add a short delay between AI calls to avoid hitting rate limits and give a better user experience
        analysis = analyse_email(sender, subject, body[:3000])

        # Step 6 — Package the email data and analysis together
        # We combine the raw email data and the AI analysis into a single dictionary
        # This makes it easy to display everything together in the dashboard template
        emails.append({
            "subject": subject,
            "sender": sender,
            "date": date,
            "body": body[:3000],

            # AI analysis results
            "priority": analysis.get("priority", "Normal"),
            "spam": analysis.get("spam", "Legitimate"),
            "summary": analysis.get("summary", "No summary available"),
            "action_required": analysis.get("action_required", "No action required"),
            "response_needed": analysis.get("response_needed", "NO")
        })

    # Sort emails by priority — Urgent first, then Normal, then Low
    # This runs AFTER the loop finishes — once all emails are collected
    # We define a custom order using a dictionary
    priority_order = {"Urgent": 0, "Normal": 1, "Low": 2}

    # Sort the emails list using the priority order
    # Emails with unknown priority go to the end with default value 99
    emails.sort(key=lambda x: priority_order.get(x["priority"], 99))

    # Pass the complete list of emails to the dashboard template to display
    return render_template("dashboard.html", emails=emails)

# Logout route — clears the session and redirects to home page
@app.route("/logout")
def logout():
    # Remove all session data — credentials are cleared, user is logged out
    session.clear()
    # Send user back to home page
    return redirect(url_for("index"))

# Run the app only when this file is executed directly — not when imported
if __name__ == "__main__":
    # Start the Flask development server with debug mode enabled
    # debug=True auto-restarts on code changes and shows detailed errors
    app.run(debug=True)