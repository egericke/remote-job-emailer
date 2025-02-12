import os
import json
import re
import requests
from bs4 import BeautifulSoup
from datetime import timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Load Configuration from config.json ---
try:
    with open("config.json", "r") as f:
        config = json.load(f)
except Exception as e:
    logger.error(f"Could not load config.json: {e}")
    config = {}

MAX_PAGES = config.get("max_pages", 10)
TIMEOUT = config.get("timeout", 10)
RETRIES = config.get("retries", 3)
KEYWORD_FILTER = config.get("keyword_filter", "improvement")

# --- Load SMTP Credentials from Environment Variables ---
SMTP_SERVER = os.environ.get("SMTP_SERVER")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL")

JOB_LISTINGS_URL = "https://www.remotefront.com/remote-jobs"

# --- Cache for Job Descriptions ---
job_desc_cache = {}

def get_with_retries(url, headers, timeout=TIMEOUT, retries=RETRIES):
    """Fetch a URL with retries."""
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            if response.status_code == 200:
                return response
            else:
                logger.warning(f"Attempt {attempt}: Received status {response.status_code} for {url}")
        except Exception as e:
            logger.warning(f"Attempt {attempt}: Exception fetching {url}: {e}")
    logger.error(f"Failed to fetch {url} after {retries} attempts.")
    return None

def parse_relative_time(text):
    """
    Expects a string like "4 hours ago" or "15 minutes ago" and returns a timedelta.
    If parsing fails, returns a very large timedelta.
    """
    match = re.search(r'(\d+)\s*(minutes?|hours?|days?)\s+ago', text.lower())
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
    Fetches job listings by scanning list items (<li>) for links that begin with "/remote-jobs/".
    Extracts the title from the link and the relative time by scanning the full text for patterns like "4 hours ago".
    Pagination is handled using a "Next" link.
    """
    listings = []
    url = JOB_LISTINGS_URL
    visited_urls = set()
    page_count = 0
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/115.0 Safari/537.36")
    }

    while url and page_count < MAX_PAGES:
        if url in visited_urls:
            break
        visited_urls.add(url)
        page_count += 1
        logger.info(f"Fetching page {page_count}: {url}")
        response = get_with_retries(url, headers, timeout=TIMEOUT, retries=RETRIES)
        if not response:
            break
        soup = BeautifulSoup(response.content, 'html.parser')
        # Look for <li> elements that contain an <a> whose href starts with "/remote-jobs/"
        job_cards = []
        for li in soup.find_all("li"):
            a_tag = li.find("a", href=re.compile(r"^/remote-jobs/"))
            if a_tag:
                job_cards.append(li)
        logger.info(f"Found {len(job_cards)} job cards on page {page_count}")
        page_has_new_jobs = False
        for card in job_cards:
            a_tag = card.find("a", href=re.compile(r"^/remote-jobs/"))
            if not a_tag:
                continue
            title = a_tag.get_text(strip=True)
            # Extract relative time by searching the full text of the card.
            card_text = card.get_text(separator=" ", strip=True)
            time_match = re.search(r'(\d+\s*(minutes?|hours?|days?)\s+ago)', card_text, re.IGNORECASE)
            if time_match:
                relative_time = time_match.group(1)
            else:
                relative_time = ""
            parsed_time = parse_relative_time(relative_time) if relative_time else timedelta.max
            logger.debug(f"Job '{title}' relative time: '{relative_time}' parsed as {parsed_time}")
            if parsed_time <= timedelta(days=1):
                page_has_new_jobs = True
            listings.append({
                "title": title,
                "detail_url": ("https://www.remotefront.com" + a_tag["href"]) if a_tag["href"].startswith("/") else a_tag["href"],
                "relative_time": relative_time
            })
        if not page_has_new_jobs:
            logger.info("No new jobs found on this page; stopping pagination.")
            break
        # Handle pagination by looking for a "Next" link
        next_link = soup.find("a", string=lambda text: text and "next" in text.lower())
        if next_link and next_link.has_attr("href"):
            next_url = next_link["href"]
            if next_url.startswith("/"):
                next_url = "https://www.remotefront.com" + next_url
            url = next_url
        else:
            break
    logger.info(f"Total jobs fetched: {len(listings)}")
    return listings

def fetch_job_description(job_url):
    """
    Fetches the job detail page and extracts the job description.
    Uses caching to avoid redundant HTTP calls.
    """
    if job_url in job_desc_cache:
        return job_desc_cache[job_url]
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/115.0 Safari/537.36")
    }
    response = get_with_retries(job_url, headers, timeout=TIMEOUT, retries=RETRIES)
    if not response:
        job_desc_cache[job_url] = "Could not fetch job details."
        return job_desc_cache[job_url]
    try:
        soup = BeautifulSoup(response.content, 'html.parser')
        description_div = soup.find("div", class_="job-description")
        if description_div:
            description = description_div.get_text(separator="\n", strip=True)
        else:
            description = soup.get_text(separator="\n", strip=True)
        job_desc_cache[job_url] = description
        return description
    except Exception as e:
        logger.error(f"Error fetching job description from {job_url}: {e}")
        job_desc_cache[job_url] = f"Error fetching job description: {e}"
        return job_desc_cache[job_url]

def filter_recent_jobs(listings, keyword=KEYWORD_FILTER):
    """
    Filters job listings to include only those posted within the last 24 hours
    and whose title (or, if necessary, job description) contains the given keyword.
    """
    recent_jobs = []
    for job in listings:
        rt_text = job.get("relative_time", "")
        parsed_time = parse_relative_time(rt_text) if rt_text else timedelta.max
        if parsed_time <= timedelta(days=1):
            # Check if the keyword appears in the title.
            if keyword.lower() in job.get("title", "").lower():
                recent_jobs.append(job)
            else:
                # If not in the title, check the job description.
                if job.get("detail_url"):
                    description = fetch_job_description(job["detail_url"])
                    if keyword.lower() in description.lower():
                        job["description"] = description  # Cache in job object.
                        recent_jobs.append(job)
    logger.info(f"Jobs after filtering by keyword '{keyword}': {len(recent_jobs)}")
    return recent_jobs

def compose_email(jobs):
    """
    Composes an HTML email with the job listings and their descriptions.
    """
    html = "<html><body>"
    html += f"<h1>RemoteFront Job Listings (Filtered: '{KEYWORD_FILTER}') from the Past Day</h1>"
    if not jobs:
        html += "<p>No new jobs matching the filter were posted in the past day.</p>"
    else:
        for job in jobs:
            html += f"<h2>{job['title']}</h2>"
            html += f"<p>View Posting: <a href='{job['detail_url']}'>{job['detail_url']}</a></p>"
            if "description" in job:
                description = job["description"]
            else:
                description = fetch_job_description(job["detail_url"])
            html += f"<p>{description}</p><hr/>"
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
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=TIMEOUT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        server.quit()
        logger.info("Email sent successfully.")
    except Exception as e:
        logger.error(f"Error sending email: {e}")

def main():
    try:
        listings = fetch_job_listings()
        recent_jobs = filter_recent_jobs(listings, keyword=KEYWORD_FILTER)
        email_body = compose_email(recent_jobs)
        send_email("Daily RemoteFront Job Listings (Filtered)", email_body)
    except Exception as e:
        logger.error(f"An error occurred in main: {e}")

if __name__ == "__main__":
    main()
