import argparse
import logging
import sys
from typing import List, Optional

import config
from config import SHEET_COLUMNS, TARGET_KEYWORDS
from sheets_manager import GoogleSheetsManager
from indiamart_scraper import IndiaMARTScraper
from gem_scraper import GeMPortalScraper
from eproc_scraper import EProcureScraper

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.BASE_DIR / "tender_pipeline.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("tender_pipeline")

def is_relevant_tender(title: str, description: str) -> bool:
    """
    Validation gate to verify tender relevance against the TARGET_KEYWORDS whitelist.
    Returns True if any whitelisted keyword is present in combined title and description.
    """
    combined_text = f"{title} {description}".lower()
    return any(keyword.lower() in combined_text for keyword in TARGET_KEYWORDS)

class TenderAutomationPipeline:
    """
    Orchestrates live tender discovery across GeM BidPlus, Central eProcure (CPPP), and IndiaMART.
    Enforces strict active deadlines (Buffer >= 3 days) and authentic verified metadata.
    """

    def __init__(
        self,
        keywords: Optional[List[str]] = None,
        dry_run: bool = False,
        headless: bool = config.HEADLESS,
        portal: str = "all"  # 'gem', 'eproc', 'indiamart', or 'all'
    ):
        self.keywords = keywords or TARGET_KEYWORDS
        self.dry_run = dry_run
        self.headless = headless
        self.portal = portal.lower()
        self.gem_scraper = GeMPortalScraper(headless=headless)
        self.eproc_scraper = EProcureScraper(headless=headless)
        self.indiamart_scraper = IndiaMARTScraper(headless=headless)
        self.sheets_manager: Optional[GoogleSheetsManager] = None
        self._init_sheets()

    def _init_sheets(self) -> None:
        if self.dry_run:
            logger.info("[DRY-RUN MODE] Skipping Google Sheets connection.")
            return

        if config.WEBHOOK_URL:
            logger.info(f"Connected to Free Google Sheets Webhook: {config.WEBHOOK_URL[:45]}...")
            return

        try:
            self.sheets_manager = GoogleSheetsManager()
            logger.info("Connected to Google Sheets via Service Account.")
        except FileNotFoundError as fnf:
            logger.warning("No Service Account found. Running in DRY-RUN mode.")
            self.dry_run = True

    def run(self, max_pages: int = 2) -> None:
        """
        Executes unified tender discovery across GeM, eProcure, and IndiaMART with buffer and keyword filtering.
        """
        logger.info("\n" + "=" * 60)
        logger.info("STARTING UNIFIED TENDER DISCOVERY PIPELINE")
        logger.info(f"Target Whitelist Keywords ({len(self.keywords)}): {self.keywords}")
        logger.info(f"Minimum Preparation Window: >= {config.MIN_DAYS_LEFT_BUFFER} Days")
        logger.info(f"Portals: {self.portal.upper()}")
        logger.info(f"Mode: {'DRY RUN' if self.dry_run else 'LIVE GOOGLE SHEETS'}")
        logger.info("=" * 60 + "\n")

        # 1. Load existing IDs to prevent duplicates
        existing_ids = set()
        if self.sheets_manager and not self.dry_run:
            existing_ids = self.sheets_manager.get_existing_gem_ids()
            logger.info(f"Loaded {len(existing_ids)} existing tender IDs from Google Sheet.")

        discovered_tenders: List[dict] = []
        discovered_ids = set()

        # 2. Iterate through keywords
        for idx, kw in enumerate(self.keywords, 1):
            logger.info(f"\n==================================================")
            logger.info(f"[{idx}/{len(self.keywords)}] SEARCHING FOR: '{kw}'")
            logger.info(f"==================================================")

            # Portal 1: GeM BidPlus Portal
            if self.portal in ("all", "gem"):
                gem_results = self.gem_scraper.search_gem_by_keyword(kw)
                for t in gem_results:
                    gid = t.get("GeM ID", "").strip()
                    title = t.get("Product Description", "") or t.get("Tender Description", "")
                    desc = t.get("Tender Description", "")

                    # Validation gate: Strict keyword filtering
                    if not is_relevant_tender(title, desc):
                        logger.info(f"Discarding irrelevant GeM tender [{gid}]: '{title[:40]}' does not match TARGET_KEYWORDS")
                        continue

                    if gid and gid not in discovered_ids:
                        discovered_ids.add(gid)
                        discovered_tenders.append(t)

            # Portal 2: Central e-Procurement Portal (CPPP)
            if self.portal in ("all", "eproc"):
                eproc_results = self.eproc_scraper.search_eproc_by_keyword(kw)
                for t in eproc_results:
                    eid = t.get("GeM ID", "").strip()
                    title = t.get("Product Description", "") or t.get("Tender Description", "")
                    desc = t.get("Tender Description", "")

                    # Validation gate: Strict keyword filtering
                    if not is_relevant_tender(title, desc):
                        logger.info(f"Discarding irrelevant eProc tender [{eid}]: '{title[:40]}' does not match TARGET_KEYWORDS")
                        continue

                    if eid and eid not in discovered_ids:
                        discovered_ids.add(eid)
                        discovered_tenders.append(t)

            # Portal 3: IndiaMART Leads to GeM Pipeline
            if self.portal in ("all", "indiamart"):
                im_result = self.indiamart_scraper.search_keyword(kw)
                for item in im_result.get("items", []):
                    item_id = item.get("gem_id", "").strip()
                    item_title = item.get("keyword", "")
                    item_desc = item.get("card_snippet", "")

                    # Validation gate: Check before passing any scraped item to the GeM scraper
                    if not is_relevant_tender(item_title, item_desc):
                        logger.info(f"Discarding irrelevant IndiaMART lead [{item_id}]: '{item_desc[:40]}' does not match TARGET_KEYWORDS")
                        continue

                    if item_id and item_id not in discovered_ids and item_id not in existing_ids:
                        logger.info(f"Passing validated IndiaMART lead [{item_id}] to GeM Scraper...")
                        gem_data = self.gem_scraper.scrape_tender_by_id(item_id)
                        if gem_data:
                            g_title = gem_data.get("Product Description", "") or gem_data.get("Tender Description", "")
                            g_desc = gem_data.get("Tender Description", "")
                            if not is_relevant_tender(g_title, g_desc):
                                logger.info(f"Discarding scraped GeM tender [{item_id}]: '{g_title[:40]}' does not match TARGET_KEYWORDS")
                                continue
                            discovered_ids.add(item_id)
                            discovered_tenders.append(gem_data)

        logger.info(f"\nTotal Active Verified Tenders Discovered: {len(discovered_tenders)}")

        # 3. Filter out existing records
        new_tenders = [t for t in discovered_tenders if t.get("GeM ID") not in existing_ids]
        logger.info(f"New unique Tenders to process: {len(new_tenders)}")

        if not new_tenders:
            logger.info("No new tenders to append. Pipeline finished.")
            return

        # 4. Output or Append to Google Sheet (via Free Webhook or Service Account)
        appended_count = 0
        for idx, tender in enumerate(new_tenders, 1):
            gid = tender.get("GeM ID")
            actual_url = tender.get("Source URL", "Not Found")
            print(f"[LINK VERIFICATION] GeM ID: {gid} | Extracted URL: {actual_url}")
            logger.info(f"\n[{idx}/{len(new_tenders)}] Processing Tender: {gid} - {tender.get('Tender Description', '')}")

            if not self.dry_run:
                posted = False
                # 1. Try Free Google Apps Script Webhook first
                if config.WEBHOOK_URL:
                    try:
                        import requests
                        doc_link = tender.get("Tender URL") or tender.get("Source URL") or config.GEM_PORTAL_SEARCH_URL
                        payload = {
                            "gemId": tender.get("GeM ID", ""),
                            "description": tender.get("Tender Description", ""),
                            "buyer": tender.get("Buyer Name", ""),
                            "startDate": tender.get("Start Date", tender.get("Tender Release Date", "")),
                            "endDate": tender.get("End Date to Participate", ""),
                            "daysLeft": tender.get("Days Left", ""),
                            "openingDate": tender.get("Tender Opening Date", ""),
                            "value": tender.get("Tender Value", "Refer Bid Document"),
                            "city": tender.get("City", ""),
                            "category": tender.get("Seller Category", ""),
                            "eligibility": tender.get("Eligibility Criteria", ""),
                            "delivery": tender.get("Work Completion Date", ""),
                            "tenderUrl": doc_link,
                            "sourceUrl": doc_link,
                            "assignedTo": tender.get("Assigned To", getattr(config, "DEFAULT_ASSIGNED_TO", "marketing@cryocorp.co.in"))
                        }
                        res = requests.post(config.WEBHOOK_URL, json=payload, timeout=15)
                        if res.status_code == 200:
                            logger.info(f"Successfully posted {gid} to Google Sheet via Free Webhook!")
                            appended_count += 1
                            posted = True
                    except Exception as e:
                        logger.warning(f"Webhook post notice: {e}")

                # 2. Fallback to Service Account if available
                if not posted and self.sheets_manager:
                    success = self.sheets_manager.append_tender(tender)
                    if success:
                        appended_count += 1
            else:
                logger.info(f"[DRY-RUN RESULT] Tender Data:\n{tender}")
                appended_count += 1

        # Summary Report
        logger.info("\n" + "=" * 60)
        logger.info("PIPELINE EXECUTION SUMMARY")
        logger.info(f"Keywords Searched: {len(self.keywords)}")
        logger.info(f"Total Active Tenders Discovered: {len(discovered_ids)}")
        logger.info(f"New Tenders Processed: {len(new_tenders)}")
        logger.info(f"Records Added to Sheet: {appended_count}")
        logger.info("=" * 60)

    def process_single_gem_id(self, gem_id: str) -> None:
        """Processes a single GeM ID directly (useful for ad-hoc extraction or testing)."""
        logger.info(f"Processing single GeM ID: {gem_id}")
        tender_details = self.gem_scraper.scrape_tender_by_id(gem_id)
        if not tender_details:
            logger.error(f"Failed to scrape details for {gem_id}")
            return

        title = tender_details.get("Product Description", "") or tender_details.get("Tender Description", "")
        desc = tender_details.get("Tender Description", "")
        if not is_relevant_tender(title, desc):
            logger.warning(f"Discarding tender {gem_id}: '{title[:40]}' does not match TARGET_KEYWORDS whitelist.")
            return

        if not self.dry_run and self.sheets_manager:
            self.sheets_manager.append_tender(tender_details)
        else:
            logger.info(f"[DRY-RUN RESULT] Tender:\n{tender_details}")

def main():
    parser = argparse.ArgumentParser(description="Unified GeM, eProcure & IndiaMART Tender Automation Pipeline")
    parser.add_argument("--keywords", nargs="+", help="Custom keywords to search", default=None)
    parser.add_argument("--portal", type=str, choices=["gem", "eproc", "indiamart", "all"], default="all", help="Target portal: 'gem', 'eproc', 'indiamart', or 'all'")
    parser.add_argument("--gem-id", type=str, help="Process a single GeM ID directly", default=None)
    parser.add_argument("--repair-links", action="store_true", help="Audit and retroactively repair invalid bid document links in Google Sheets")
    parser.add_argument("--max-pages", type=int, help="Maximum pages per keyword", default=2)
    parser.add_argument("--dry-run", action="store_true", help="Run without connecting to Google Sheets")
    parser.add_argument("--headed", action="store_true", help="Run browser in visible (headed) mode")

    args = parser.parse_args()

    if args.repair_links:
        from repair_sheet_links import RetroactiveLinkRepairer
        repairer = RetroactiveLinkRepairer(
            dry_run=args.dry_run,
            headless=not args.headed
        )
        repairer.repair_links(specific_ids=[args.gem_id] if args.gem_id else None)
        return

    pipeline = TenderAutomationPipeline(
        dry_run=args.dry_run,
        headless=not args.headed,
        keywords=args.keywords,
        portal=args.portal
    )

    if args.gem_id:
        pipeline.process_single_gem_id(args.gem_id)
    else:
        pipeline.run(max_pages=args.max_pages)

if __name__ == "__main__":
    main()
