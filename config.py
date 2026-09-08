import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

# Google Sheets Configuration
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://script.google.com/macros/s/AKfycbzDQ4akTf5nm7egv5ZaYRdHL2xD8uS8EKLdSb_nrUKR_Ti7j2nZfAX6-eSL202Cmk6k/exec")
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", str(BASE_DIR / "service_account.json"))
SPREADSHEET_NAME_OR_ID = os.getenv("SPREADSHEET_NAME_OR_ID", "GEM TEN")
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "Cryocorp tenders")

# Google Sheets Headers (Aligned with Ma'am's layout: Col N = Tender URL, Col O = Assigned To)
SHEET_COLUMNS = [
    "Scraped Date",            # Col A
    "GeM ID",                  # Col B
    "Tender Description",      # Col C
    "Buyer Name",              # Col D
    "Tender Release Date",     # Col E
    "End Date to Participate", # Col F (embeds Days Left)
    "Tender Opening Date",     # Col G
    "Work Completion Date",    # Col H
    "Product Description",     # Col I
    "Seller Category",         # Col J
    "Tender Value",            # Col K
    "City",                    # Col L
    "Eligibility Criteria",    # Col M
    "Tender URL",              # Col N (Direct PDF link or portal search)
    "Assigned To"              # Col O (Pre-filled with marketing@cryocorp.co.in)
]

DEFAULT_ASSIGNED_TO = os.getenv("DEFAULT_ASSIGNED_TO", "marketing@cryocorp.co.in")


# Strict Whitelist Target Keywords
TARGET_KEYWORDS = [
    "cooling tower",
    "cooling towers",
    "frp cooling tower",
    "oxygen",
    "nitrogen",
    "cryogenic",
    "gas analyzer"
]

# Backward compatibility alias
INDIAMART_KEYWORDS = TARGET_KEYWORDS

# Minimum Days Left Buffer (Exclude expired and tenders expiring within 1-2 days)
MIN_DAYS_LEFT_BUFFER = int(os.getenv("MIN_DAYS_LEFT_BUFFER", "3"))

# Scraping & Portal Settings
GEM_PORTAL_BASE_URL = "https://bidplus.gem.gov.in"
GEM_PORTAL_SEARCH_URL = "https://bidplus.gem.gov.in/all-bids"
EPROC_PORTAL_BASE_URL = "https://eprocure.gov.in"
EPROC_PORTAL_SEARCH_URL = "https://eprocure.gov.in/eprocure/app"
GEM_ID_REGEX = r"GEM/\d{4}/[A-Z]/\d+"
EPROC_REF_REGEX = r"[A-Za-z0-9\/\-\_\.]{4,40}"

# Browser Settings
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
BROWSER_TIMEOUT_MS = int(os.getenv("BROWSER_TIMEOUT_MS", "30000"))

# PDF Download Settings
PDF_DOWNLOAD_DIR = BASE_DIR / "downloads"
AUTO_DOWNLOAD_PDFS = os.getenv("AUTO_DOWNLOAD_PDFS", "true").lower() == "true"

