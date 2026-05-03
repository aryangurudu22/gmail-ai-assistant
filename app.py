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

    # Fetch list of unread emails from the user's inbox
    # maxResults=10 limits to 10 emails for now — we increase this later
    results = gmail_service.users().messages().list(
        userId="me",
        labelIds=["UNREAD"],
        maxResults=10
    ).execute()

    # Get the list of messages or empty list if no unread emails found
    messages = results.get("messages", [])

    # Pass messages to the dashboard template to display
    return render_template("dashboard.html", messages=messages)

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