from flask import Flask, render_template
from dotenv import load_dotenv
import os

# Load environment variables from .env file into memory
load_dotenv()
# Create the Flask application instance
app = Flask(__name__)

# Load the Flask secret key from environment variables
app.secret_key = os.getenv("FLASK_SECRET_KEY")

# Home page route — the first page a user sees when they visit the app
@app.route("/")

# Index function — handles requests to the home page and returns the HTML template
def index():
    # Find index.html in the templates folder and send it to the user's browser
    return render_template("index.html")

# Run the app only when this file is executed directly — not when imported
if __name__ == "__main__":
    # Start the Flask development server with debug mode enabled
    app.run(debug=True)
