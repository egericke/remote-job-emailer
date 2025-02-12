import os
import requests
from bs4 import BeautifulSoup
from datetime import timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import re

# =========================
# Configuration – load from environment variables
# =========================
SMTP_SERVER = os.environ.get("SMTP_SERVER")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL")

JOB_LISTINGS_URL = "https://www.remotefront.com/remote-jobs"


JOB_LISTINGS_URL = "https://www.remotefront.com/remote-jobs"

# =========================
# Helper Functions
# =========================
def parse_relative_time(text):
    """
    Parses a relative time string (e.g. "about 2 hours", "6 minutes")
    and returns a timedelta.
    """
    match = re.search(r'(\d+)\s*(minute|hour|day)', text.lower())
    if match:
        num = int(match.group(1))
        unit = match.group(2)
        if "minute" in unit:
            return timedelta(minutes=num)
        elif "hour" in unit:
            return timedelta(hours=num)
        elif "day" in unit:
            return timedelta(days=num)
    return timedelta.max

def fetch_job_listings():
    """
    Fetches the job listings from RemoteFront.
    Returns a list of dictionaries with keys: 'title', 'detail_url', and 'relative_time'.
    """
    listings = []
    response = requests.get(JOB_LISTINGS_URL)
    if response.status_code != 200:
        print("Failed to fetch job listings.")
        return listings

    soup = BeautifulSoup(response.content, 'html.parser')

    # Try to select each job posting card.
    job_cards = soup.find_all("div", class_="job-card")
    if not job_cards:
        # Fallback: try looking for <article> elements.
        job_cards = soup.find_all("article")

    for card in job_cards:
        # Extract the job title (look for header tags like <h2> or <h3>).
        title_tag = card.find(["h2", "h3"])
        if not title_tag:
            continue
        title = title_tag.get_text(strip=True)

        # Extract the detail URL from an <a> tag within the title.
        link_tag = title_tag.find("a")
        if link_tag and link_tag.has_attr("href"):
            detail_url = link_tag["href"]
            if detail_url.startswith("/"):
                detail_url = "https://www.remotefront.com" + detail_url
        else:
            detail_url = None

        # Look for an element that contains the relative time (e.g. "6 minutes", "2 hours")
        time_tag = card.find(lambda tag: tag.name in ["span", "time"] and (
            "minute" in tag.get_text().lower() or 
            "hour" in tag.get_text().lower() or 
            "day" in tag.get_text().lower()))
        relative_time = time_tag.get_text(strip=True) if time_tag else ""

        listings.append({
            "title": title,
            "detail_url": detail_url,
            "relative_time": relative_time
        })
    return listings

def fetch_job_description(job_url):
    """
    Fetches the job detail page and extracts the job description.
    """
    try:
        response = requests.get(job_url)
        if response.status_code != 200:
            return "Could not fetch job details."
        soup = BeautifulSoup(response.content, 'html.parser')
        description_div = soup.find("div", class_="job-description")
        if description_div:
            return description_div.get_text(separator="\n", strip=True)
        else:
            return soup.get_text(separator="\n", strip=True)
    except Exception as e:
        return f"Error fetching job description: {e}"

def filter_recent_jobs(listings):
    """
    Filters the job listings to only include those posted within the last 24 hours.
    """
    recent_jobs = []
    for job in listings:
        rt_text = job.get("relative_time", "")
        if parse_relative_time(rt_text) <= timedelta(days=1):
            recent_jobs.append(job)
    return recent_jobs

def compose_email(jobs):
    """
    Composes an HTML email with the job listings and their descriptions.
    """
    html = "<html><body>"
    html += "<h1>RemoteFront Job Listings from the Past Day</h1>"
    if not jobs:
        html += "<p>No new jobs were posted in the past day.</p>"
    else:
        for job in jobs:
            html += f"<h2>{job['title']}</h2>"
            if job.get("detail_url"):
                html += f"<p>View Posting: <a href='{job['detail_url']}'>{job['detail_url']}</a></p>"
                description = fetch_job_description(job["detail_url"])
                html += f"<p>{description}</p>"
            html += "<hr/>"
    html += "</body></html>"
    return html

def send_email(subject, html_content):
    """
    Sends an email with the specified subject and HTML content.
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL
    msg.attach(MIMEText(html_content, "html"))

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        server.quit()
        print("Email sent successfully.")
    except Exception as e:
        print(f"Error sending email: {e}")

# =========================
# Main Process
# =========================
def main():
    listings = fetch_job_listings()
    recent_jobs = filter_recent_jobs(listings)
    email_body = compose_email(recent_jobs)
    send_email("Daily RemoteFront Job Listings", email_body)

if __name__ == "__main__":
    main()
