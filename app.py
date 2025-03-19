import os
import time
import imaplib
import email
import threading
import smtplib
from email.header import decode_header
from email.mime.text import MIMEText
from flask import Flask, render_template
from flask_socketio import SocketIO
from dotenv import load_dotenv
from openai import OpenAI
import datetime
import json

# Load environment variables
load_dotenv()

# Flask app & SocketIO
app = Flask(__name__)
socketio = SocketIO(app)

# Email Credentials
EMAIL = os.getenv("EMAIL")
APP_PASSWORD = os.getenv("APP_PASSWORD")
IMAP_SERVER = os.getenv("IMAP_SERVER")
SMTP_SERVER = os.getenv("SMTP_SERVER")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# OpenAI Client
client = OpenAI(api_key=OPENAI_API_KEY)

# Track processed emails
processed_emails = set()

# Load processed emails from a file (if it exists)
def load_processed_emails():
    try:
        with open("processed_emails.json", "r") as file:
            return set(json.load(file))
    except FileNotFoundError:
        return set()

# Save processed emails to a file
def save_processed_emails():
    with open("processed_emails.json", "w") as file:
        json.dump(list(processed_emails), file)

# Load processed emails at startup
processed_emails = load_processed_emails()

def fetch_new_emails():
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL, APP_PASSWORD)
        mail.select("inbox")

        # Get today's date in the format required by IMAP (e.g., "01-Jan-2023")
        today_date = datetime.datetime.now().strftime("%d-%b-%Y")

        # Fetch unread emails since today
        _, messages = mail.search(None, f'UNSEEN SINCE "{today_date}"')  
        new_emails = []

        print(f"📬 Found {len(messages[0].split())} new emails since {today_date}")  # Debugging

        for num in messages[0].split():
            email_id = num.decode("utf-8")
            if email_id in processed_emails:
                continue  # Skip already processed emails

            _, msg_data = mail.fetch(num, "(RFC822)")
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    subject, encoding = decode_header(msg["Subject"])[0]
                    if isinstance(subject, bytes):
                        subject = subject.decode(encoding or "utf-8")
                    from_email = msg.get("From")

                    # Extract the email body
                    body = ""
                    if msg.is_multipart():
                        # Iterate over email parts
                        for part in msg.walk():
                            content_type = part.get_content_type()
                            content_disposition = str(part.get("Content-Disposition"))

                            # Skip attachments
                            if "attachment" in content_disposition:
                                continue

                            # Extract plain text or HTML content
                            if content_type == "text/plain":
                                body = part.get_payload(decode=True).decode()
                            elif content_type == "text/html":
                                body = part.get_payload(decode=True).decode()
                    else:
                        # If not multipart, extract the payload directly
                        body = msg.get_payload(decode=True).decode()

                    print(f"📩 New Email - From: {from_email}, Subject: {subject}")  # Debugging
                    print(f"📄 Body: {body[:100]}...")  # Debugging (print first 100 chars of the body)

                    # Mark email as processed
                    processed_emails.add(email_id)
                    new_emails.append({
                        "id": email_id,
                        "subject": subject,
                        "from": from_email,
                        "body": body  # Include the email body
                    })

                    # Mark the email as "Seen" on the server
                    mail.store(num, "+FLAGS", "\\Seen")

        mail.close()
        mail.logout()

        # Save processed emails to file
        save_processed_emails()

        return new_emails
    except Exception as e:
        print(f"⚠️ Error fetching emails: {e}")
        return []

def generate_reply(email_data):
    try:
        prompt = f"""
        You are a professional email assistant. Draft a reply to the following email:

        From: {email_data['from']}
        Subject: {email_data['subject']}
        Body: {email_data['body']}

        Write a concise and professional reply:
        """

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": "You are a professional email assistant."},
                      {"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error generating reply: {str(e)}"

def send_email(to_email, subject, body):
    try:
        # Create the email message
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = EMAIL
        msg["To"] = to_email

        # Establish a secure connection with the SMTP server
        with smtplib.SMTP_SSL(SMTP_SERVER, 465) as server:
            server.login(EMAIL, APP_PASSWORD)  # Log in to the SMTP server
            server.sendmail(EMAIL, to_email, msg.as_string())  # Send the email
            print(f"📤 Email sent to {to_email}")  # Debugging
        return True
    except Exception as e:
        print(f"⚠️ Error sending email: {str(e)}")
        return False

def monitor_emails():
    while True:
        new_emails = fetch_new_emails()
        for email_data in new_emails:
            socketio.emit("new_email", email_data)  # Send new email to frontend

            # Generate a reply using the complete email data
            reply = generate_reply(email_data)

            # Send the reply
            if send_email(email_data["from"], f"Re: {email_data['subject']}", reply):
                print(f"✅ Replied to {email_data['from']}")
                socketio.emit("remove_email", email_data["id"])  # Remove email from UI
            else:
                print(f"❌ Failed to reply to {email_data['from']}")

        time.sleep(5)  # Poll every 5 seconds

@app.route("/")
def index():
    return render_template("index.html")

if __name__ == "__main__":
    threading.Thread(target=monitor_emails, daemon=True).start()
    socketio.run(app, debug=True)