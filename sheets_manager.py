import os
import sys
import re
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Set
import gspread
from google.oauth2.service_account import Credentials

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Required Google OAuth Scopes for Spreadsheet & Drive operations
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

class GoogleSheetsManager:
    """
    Manages Google Sheets authentication and appending scraped tender records.
    Authenticates via a Google Cloud Service Account JSON file.
    """

    def __init__(
        self,
        service_account_path: Optional[str] = None,
        spreadsheet_name_or_id: Optional[str] = None,
        worksheet_name: Optional[str] = None
    ):
        self.service_account_path = service_account_path or config.SERVICE_ACCOUNT_FILE
        self.spreadsheet_name_or_id = spreadsheet_name_or_id or config.SPREADSHEET_NAME_OR_ID
        self.worksheet_name = worksheet_name or config.WORKSHEET_NAME
        self.client: Optional[gspread.Client] = None
        self.spreadsheet: Optional[gspread.Spreadsheet] = None
        self.worksheet: Optional[gspread.Worksheet] = None

        self._authenticate()

    def _authenticate(self) -> None:
        """Authenticates with the Google Sheets API using Service Account credentials."""
        if not os.path.exists(self.service_account_path):
            raise FileNotFoundError(
                f"Service account file not found at: '{self.service_account_path}'.\n"
                "Please place your Google Cloud Service Account JSON key at this path "
                "or set the GOOGLE_SERVICE_ACCOUNT_FILE environment variable."
            )

        try:
            logger.info(f"Authenticating with Google API using {self.service_account_path}...")
            credentials = Credentials.from_service_account_file(
                self.service_account_path,
                scopes=SCOPES
            )
            self.client = gspread.authorize(credentials)
            logger.info("Successfully authenticated with Google Sheets API.")
            self._init_worksheet()
        except Exception as e:
            logger.error(f"Failed to authenticate with Google Sheets: {e}")
            raise

    def _init_worksheet(self) -> None:
        """Opens or creates the target Google Spreadsheet and Worksheet, ensuring headers exist."""
        try:
            # Try opening by key/id first, if not then by title/name
            if self.spreadsheet_name_or_id.startswith("1") and len(self.spreadsheet_name_or_id) > 30:
                self.spreadsheet = self.client.open_by_key(self.spreadsheet_name_or_id)
            else:
                self.spreadsheet = self.client.open(self.spreadsheet_name_or_id)
        except gspread.SpreadsheetNotFound:
            # If the spreadsheet doesn't exist, create it (works if service account has drive permission)
            logger.warning(f"Spreadsheet '{self.spreadsheet_name_or_id}' not found. Creating a new spreadsheet...")
            self.spreadsheet = self.client.create(self.spreadsheet_name_or_id)
            logger.info(f"Created new spreadsheet '{self.spreadsheet_name_or_id}' (ID: {self.spreadsheet.id})")

        # Open or create worksheet
        try:
            self.worksheet = self.spreadsheet.worksheet(self.worksheet_name)
        except gspread.WorksheetNotFound:
            logger.info(f"Worksheet '{self.worksheet_name}' not found. Creating worksheet with default headers...")
            self.worksheet = self.spreadsheet.add_worksheet(
                title=self.worksheet_name,
                rows=100,
                cols=len(config.SHEET_COLUMNS)
            )

        # Check if headers exist; if empty, insert them
        existing_headers = self.worksheet.row_values(1)
        if not existing_headers:
            logger.info(f"Inserting standard header row into worksheet '{self.worksheet_name}'...")
            self.worksheet.insert_row(config.SHEET_COLUMNS, index=1)
            # Format header with bold text
            try:
                self.worksheet.format("A1:N1", {"textFormat": {"bold": True}})
            except Exception:
                pass

    def get_existing_gem_ids(self) -> Set[str]:
        """
        Retrieves the set of already recorded GeM IDs from the sheet to avoid duplicate entries.
        """
        if not self.worksheet:
            return set()

        try:
            # GeM ID is at column 2 (B)
            gem_id_col_index = config.SHEET_COLUMNS.index("GeM ID") + 1
            col_values = self.worksheet.col_values(gem_id_col_index)
            # Skip header row
            existing_ids = {val.strip() for val in col_values[1:] if val.strip()}
            logger.info(f"Found {len(existing_ids)} existing GeM IDs in sheet '{self.worksheet_name}'.")
            return existing_ids
        except Exception as e:
            logger.warning(f"Could not read existing GeM IDs: {e}")
            return set()

    def get_all_rows_with_gem_ids(self) -> List[Dict[str, Any]]:
        """
        Retrieves all rows with their 1-based row index, GeM ID, and current Source URL.
        Useful for audits and retroactive link repairs.
        """
        if not self.worksheet:
            return []

        try:
            all_values = self.worksheet.get_all_values()
            if len(all_values) <= 1:
                return []

            headers = all_values[0]
            gem_id_col = headers.index("GeM ID") if "GeM ID" in headers else 1
            source_url_col = headers.index("Tender URL") if "Tender URL" in headers else (headers.index("Source URL") if "Source URL" in headers else 13)

            rows_data = []
            for idx, row in enumerate(all_values[1:], start=2):
                gid = row[gem_id_col].strip() if gem_id_col < len(row) else ""
                s_url = row[source_url_col].strip() if source_url_col < len(row) else ""
                if gid:
                    rows_data.append({
                        "row_idx": idx,
                        "gem_id": gid,
                        "source_url": s_url
                    })

            return rows_data
        except Exception as e:
            logger.error(f"Error fetching sheet rows: {e}")
            return []

    def update_source_url(self, row_idx: int, authentic_url: str, gem_id: str = "N/A") -> bool:
        """
        Updates the 'Tender URL' (Column N) cell of a specific row in the Google Sheet.
        Ensures the URL is written as a plain string.
        """
        if not self.worksheet:
            return False

        clean_url = str(authentic_url).strip()
        if clean_url.startswith("="):
            match = re.search(r'["\'](https?://[^"\']+)["\']', clean_url)
            clean_url = match.group(1) if match else clean_url.lstrip("=")

        if not clean_url or clean_url == "Not Found":
            clean_url = config.GEM_PORTAL_SEARCH_URL

        print(f"[LINK VERIFICATION] GeM ID: {gem_id} | Extracted URL: {clean_url}")
        logger.info(f"[LINK VERIFICATION] GeM ID: {gem_id} | Extracted URL: {clean_url}")

        try:
            if "Tender URL" in config.SHEET_COLUMNS:
                col_idx = config.SHEET_COLUMNS.index("Tender URL") + 1
            elif "Source URL" in config.SHEET_COLUMNS:
                col_idx = config.SHEET_COLUMNS.index("Source URL") + 1
            else:
                col_idx = 14  # Column N default

            self.worksheet.update_cell(row_idx, col_idx, clean_url)
            logger.info(f"Updated row {row_idx} Tender URL (Col {col_idx}) to: {clean_url}")
            return True
        except Exception as e:
            logger.error(f"Error updating Tender URL at row {row_idx}: {e}")
            return False

    def append_tender(self, tender_data: Dict[str, Any]) -> bool:
        """
        Appends a single tender dictionary to the Google Sheet.
        """
        gem_id = str(tender_data.get("GeM ID", "N/A")).strip()
        actual_doc_url = str(tender_data.get("Tender URL") or tender_data.get("Source URL") or "Not Found").strip()
        print(f"[LINK VERIFICATION] GeM ID: {gem_id} | Extracted URL: {actual_doc_url}")
        logger.info(f"[LINK VERIFICATION] GeM ID: {gem_id} | Extracted URL: {actual_doc_url}")

        row = self._format_row(tender_data)
        try:
            self.worksheet.append_row(row, value_input_option="USER_ENTERED")
            logger.info(f"Appended tender [{gem_id}] to Google Sheet successfully.")
            return True
        except Exception as e:
            logger.error(f"Error appending row for GeM ID '{gem_id}': {e}")
            return False

    def append_tenders(self, tenders_data: List[Dict[str, Any]]) -> int:
        """
        Appends multiple tender dictionaries in batch to the Google Sheet.
        Returns the number of successfully appended rows.
        """
        if not tenders_data:
            return 0

        for t in tenders_data:
            gem_id = str(t.get("GeM ID", "N/A")).strip()
            actual_doc_url = str(t.get("Tender URL") or t.get("Source URL") or "Not Found").strip()
            print(f"[LINK VERIFICATION] GeM ID: {gem_id} | Extracted URL: {actual_doc_url}")
            logger.info(f"[LINK VERIFICATION] GeM ID: {gem_id} | Extracted URL: {actual_doc_url}")

        rows = [self._format_row(t) for t in tenders_data]
        try:
            self.worksheet.append_rows(rows, value_input_option="USER_ENTERED")
            logger.info(f"Appended {len(rows)} tender records to Google Sheet.")
            return len(rows)
        except Exception as e:
            logger.error(f"Error batch appending rows: {e}")
            return 0

    def _format_row(self, data: Dict[str, Any]) -> List[Any]:
        """
        Formats the tender dictionary into the exact order of SHEET_COLUMNS.
        Ensures Column N is 'Tender URL' and Column O is 'Assigned To'.
        Embeds 'Days Left' into 'End Date to Participate' (Column F).
        Pre-fills 'Assigned To' (Column O) with 'marketing@cryocorp.co.in'.
        """
        row = []
        for col in config.SHEET_COLUMNS:
            val = data.get(col, "")
            # Support alias between "Start Date" and "Tender Release Date"
            if not val and col in ("Start Date", "Tender Release Date"):
                val = data.get("Tender Release Date") or data.get("Start Date") or ""
            elif not val and col in ("Tender URL", "Source URL"):
                val = data.get("Tender URL") or data.get("Source URL") or ""
            elif not val and col == "Assigned To":
                val = data.get("Assigned To") or getattr(config, "DEFAULT_ASSIGNED_TO", "marketing@cryocorp.co.in")

            # Embed Days Left cleanly inside End Date to Participate (Column F)
            if col == "End Date to Participate":
                days_left = data.get("Days Left")
                if days_left is not None and days_left != "N/A" and str(days_left) not in str(val):
                    try:
                        d = int(days_left)
                        if d > 1:
                            val = f"{val} ({d} Days Left)" if val else f"{d} Days Left"
                        elif d == 1:
                            val = f"{val} (1 Day Left)" if val else "1 Day Left"
                        elif d == 0:
                            val = f"{val} (Closing Today)" if val else "Closing Today"
                        elif d < 0:
                            val = f"{val} (Expired)" if val else "Expired"
                    except Exception:
                        pass

            if val is None:
                val = ""
            elif isinstance(val, (dict, list)):
                val = str(val)
            else:
                val = str(val).strip()

            # Ensure Tender URL (Column N) is written strictly as a plain string without formula injection
            if col in ("Tender URL", "Source URL"):
                if val.startswith("="):
                    url_match = re.search(r'["\'](https?://[^"\']+)["\']', val)
                    val = url_match.group(1) if url_match else val.lstrip("=")
                val = str(val).strip()
                # If URL is missing, fall back to portal search URL
                if not val or val == "Not Found":
                    val = config.GEM_PORTAL_SEARCH_URL

            row.append(val)
        return row

if __name__ == "__main__":
    # Quick sanity test demonstration
    print("Testing GoogleSheetsManager initialization...")
    try:
        manager = GoogleSheetsManager()
        sample_tender = {
            "Scraped Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "GeM ID": "GEM/2026/B/9999999",
            "Tender Description": "Supply & Installation of Cooling Tower System",
            "Buyer Name": "Department of Heavy Industry, New Delhi",
            "Start Date": "2026-08-20",
            "Tender Release Date": "2026-08-20",
            "End Date to Participate": "2026-09-15 15:00",
            "Days Left": 7,
            "Tender Opening Date": "2026-09-15 15:30",
            "Work Completion Date": "90 Days from Award",
            "Product Description": "FRP Induced Draft Cooling Tower 500 TR",
            "Seller Category": "OEM / Authorized Seller (MSE Exempted)",
            "Tender Value": "INR 45,00,000",
            "City": "New Delhi",
            "Eligibility Criteria": "Min 3 years experience in cryogenic/cooling equipment; Turnover > 50 Lakhs",
            "Source URL": "https://bidplus.gem.gov.in/showbidDocument/9999999"
        }
        manager.append_tender(sample_tender)
        print("Test row appended successfully!")
    except FileNotFoundError as fnf:
        print(f"\n[!] Note: {fnf}\nReady for service account configuration.")
