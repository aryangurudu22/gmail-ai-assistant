# Gmail AI Assistant

An AI-powered email analysis tool that connects to your Gmail account, reads your unread emails, and automatically generates a priority-sorted summary with action items for each email.

## What it does
- Connects securely to Gmail using Google OAuth
- Reads all unread emails automatically
- Uses AI to analyse each email and extract:
  - Priority level (Urgent / Normal / Low)
  - One sentence summary
  - Action required
- Displays results in a clean web dashboard

## Built with
- Python
- Flask
- Groq API (llama-3.3-70b-versatile)
- Gmail API
- Google OAuth 2.0

## Installation

1. Clone this repository
2. Install dependencies:
   pip install -r requirements.txt
3. Create a .env file with your credentials:
   GROQ_API_KEY=your_key_here
   FLASK_SECRET_KEY=your_secret_here
4. Run the app:
   python app.py
5. Open your browser at http://localhost:5000

## Project structure
- app.py — main Flask application
- templates/ — HTML pages
- static/ — CSS styling
- .env — secret keys (never shared)
- requirements.txt — project dependencies

## Author
Aryan Reddy — AI Automation Specialist