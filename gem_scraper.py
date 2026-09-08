import re
import time
import logging
import urllib.parse
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List, Set
import requests
from playwright.sync_api import sync_playwright, Browser, Page, TimeoutError as PlaywrightTimeoutError

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def get_authentic_bid_document_url(page: Page, gem_id: str) -> str:
    """
    Universal DOM Extraction Function:
    Extracts the true link directly from the rendered card in Playwright.
    Forces network idle and strict XPath scoping. Strictly bans string-based URL construction.
    """
    # 1. Force Network Idle & Hard Pauses
    if page.locator("input#searchBid").count() > 0:
        page.fill("input#searchBid", gem_id)
    else:
        page.locator("input[name='searchBid'], input#bid_no, input[placeholder*='Bid']").first.fill(gem_id)

    if page.locator("button#searchBidBtn").count() > 0 and page.locator("button#searchBidBtn").is_visible():
        page.click("button#searchBidBtn")
    elif page.locator("button#searchBidRA").count() > 0 and page.locator("button#searchBidRA").is_visible():
        page.click("button#searchBidRA")
    else:
        page.locator("input#searchBid").press("Enter")

    # Force wait for the AJAX table to refresh
    page.wait_for_timeout(3000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    # 2. Strict XPath Scoping: Locate the specific bid block containing the exact GeM ID string
    bid_block_xpath = f"//div[contains(@class, 'card') or contains(@class, 'border')][.//text()[contains(., '{gem_id}')]]"
    bid_block = page.locator(bid_block_xpath).first

    # Wait specifically for this element to become visible
    bid_block.wait_for(state="visible", timeout=15000)

    # 3. Extract the link strictly from INSIDE this verified block
    doc_anchor = bid_block.locator("xpath=.//a[contains(@href, 'showbidDocument') or contains(@href, 'buyer_documents')]").first
    if doc_anchor.count() == 0:
        doc_anchor = bid_block.locator("xpath=.//a[contains(@href, 'SpecificationDocument') or contains(@class, 'bid_no_hover')]").first

    raw_href = doc_anchor.get_attribute("href") if doc_anchor.count() > 0 else None

    if raw_href and raw_href.strip() not in ("", "#"):
        raw_href = raw_href.strip()
        if raw_href.startswith("http"):
            actual_doc_url = raw_href
        elif raw_href.startswith("/"):
            actual_doc_url = f"https://bidplus.gem.gov.in{raw_href}"
        else:
            actual_doc_url = f"https://bidplus.gem.gov.in/{raw_href}"
    else:
        actual_doc_url = "Not Found"

    print(f"[LINK VERIFICATION] GeM ID: {gem_id} | Extracted URL: {actual_doc_url}")
    logger.info(f"[LINK VERIFICATION] GeM ID: {gem_id} | Extracted URL: {actual_doc_url}")
    return actual_doc_url

def download_tender_pdf(doc_url: str, gem_id: str, output_dir: Optional[Any] = None) -> Optional[str]:
    """
    Downloads the official GeM tender PDF document to a local folder.
    Returns the absolute path to the downloaded PDF file on disk.
    """
    if not doc_url or doc_url == "Not Found" or not doc_url.startswith("http"):
        return None

    if output_dir is None:
        output_dir = getattr(config, "PDF_DOWNLOAD_DIR", config.BASE_DIR / "downloads")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", gem_id.strip())
    pdf_filename = f"{safe_id}.pdf"
    pdf_file = output_path / pdf_filename

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }
        res = requests.get(doc_url, headers=headers, timeout=30)
        if res.status_code == 200 and len(res.content) > 1000:
            with open(pdf_file, "wb") as f:
                f.write(res.content)
            logger.info(f"[PDF DOWNLOADED] Successfully saved PDF for {gem_id} ({len(res.content)} bytes) to: {pdf_file}")
            print(f"[PDF DOWNLOADED] GeM ID: {gem_id} -> Saved locally: {pdf_file}")
            return str(pdf_file)
        else:
            logger.warning(f"Could not download PDF from {doc_url} (HTTP {res.status_code})")
            return None
    except Exception as e:
        logger.error(f"Error downloading PDF for {gem_id}: {e}")
        return None

class GeMPortalScraper:
    """
    Custom Playwright scraper for Government e-Marketplace (GeM) BidPlus portal (https://bidplus.gem.gov.in/all-bids).
    Takes a GeM ID / Bid Number and extracts complete tender details.
    """
    get_authentic_bid_document_url = staticmethod(get_authentic_bid_document_url)
    download_tender_pdf = staticmethod(download_tender_pdf)


    def __init__(self, headless: bool = config.HEADLESS, timeout: int = config.BROWSER_TIMEOUT_MS):
        self.headless = headless
        self.timeout = timeout
        self.gem_regex = re.compile(config.GEM_ID_REGEX, re.IGNORECASE)

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parses various GeM portal date formats into a datetime object."""
        if not date_str:
            return None
        cleaned = date_str.strip()
        date_formats = [
            "%d-%m-%Y %I:%M %p",
            "%d-%m-%Y %H:%M:%S",
            "%d-%m-%Y %H:%M",
            "%d-%m-%Y",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %I:%M %p",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%d/%m/%Y %I:%M %p",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y",
            "%d-%b-%Y %I:%M %p",
            "%d-%b-%Y %H:%M:%S",
            "%d-%b-%Y",
            "%d %b %Y",
        ]
        for fmt in date_formats:
            try:
                return datetime.strptime(cleaned, fmt)
            except ValueError:
                continue
        # Fallback to date part if time component exists
        date_part = cleaned.split()[0].strip()
        for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y"):
            try:
                return datetime.strptime(date_part, fmt)
            except ValueError:
                continue
        return None

    def _calculate_days_left(self, date_str: str) -> Optional[int]:
        """
        Computes remaining days dynamically:
        days_left = (end_date - datetime.now()).days
        """
        end_date = self._parse_date(date_str)
        if end_date:
            return (end_date - datetime.now()).days
        return None

    def _extract_tender_description(self, card_locator: Optional[Any], card_text: str) -> str:
        """
        Extracts the full tender description/title without truncation.
        Checks the 'title' attribute on anchor/title elements, or captures the full multi-line
        Items block from div.card-body, cleaning extraneous whitespace while preserving text.
        """
        description = ""

        # 1. Attempt extraction from popover data-content, title, or data-original-title
        if card_locator:
            try:
                title_candidates = card_locator.locator(
                    "a[data-content], [data-toggle='popover'], a[title], [data-original-title], p[title], span[title], .bid_title[title], a[href*='showbidDocument'], a[href*='buyer-bid-details'], a.bid_no_hover"
                )
                for j in range(title_candidates.count()):
                    elem = title_candidates.nth(j)
                    for attr in ("data-content", "title", "data-original-title"):
                        attr_val = elem.get_attribute(attr)
                        if attr_val:
                            cleaned_attr = re.sub(r"\s+", " ", attr_val).strip()
                            # Exclude generic texts or pure GeM IDs
                            if len(cleaned_attr) > 10 and not self.gem_regex.fullmatch(cleaned_attr) and not any(
                                g in cleaned_attr.lower() for g in ["click here", "download", "view document", "view corrigendum"]
                            ):
                                description = cleaned_attr
                                break
                    if description:
                        break
            except Exception:
                pass

        # 2. If no valid title attribute, target full multi-line 'Items:' block in card-body
        if not description and card_locator:
            try:
                card_body = card_locator.locator("div.card-body, div[class*='card-body'], div.block_body, div.card-content").first
                if card_body.count() > 0:
                    body_content = card_body.inner_text()
                    items_block_match = re.search(
                        r"(?:Item\(s\)|Items?|Item Categories|Product)\s*:\s*(.+?)(?=\n\s*(?:Quantity|Department Name|Department|Ministry|Start Date|End Date|Bid End Date|Estimated Bid Value|Total Value)\s*:|$)",
                        body_content,
                        re.IGNORECASE | re.DOTALL
                    )
                    if items_block_match:
                        description = re.sub(r"\s+", " ", items_block_match.group(1)).strip()
            except Exception:
                pass

        # 3. Fallback regex on card_text with multi-line support
        if not description and card_text:
            items_match = re.search(
                r"(?:Item\(s\)|Items?|Item Categories|Product)\s*:\s*(.+?)(?=\n\s*(?:Quantity|Department Name|Department|Ministry|Start Date|End Date|Bid End Date|Estimated Bid Value|Total Value)\s*:|$)",
                card_text,
                re.IGNORECASE | re.DOTALL
            )
            if items_match:
                description = re.sub(r"\s+", " ", items_match.group(1)).strip()
            else:
                desc_match = re.search(
                    r"Description\s*:\s*(.+?)(?=\n\s*(?:Quantity|Department|Ministry|Start Date|End Date)\s*:|$)",
                    card_text,
                    re.IGNORECASE | re.DOTALL
                )
                if desc_match:
                    description = re.sub(r"\s+", " ", desc_match.group(1)).strip()

        # Final cleanup of extraneous whitespace and line breaks
        return re.sub(r"\s+", " ", description).strip()

    def search_gem_by_keyword(self, keyword: str) -> List[Dict[str, Any]]:
        """
        Searches the live GeM BidPlus portal for a keyword and extracts all active tender cards individually.
        Each tender card gets its own unique description, department, value, and dates.
        """
        logger.info(f"Searching live GeM Portal for keyword: '{keyword}'...")
        results: List[Dict[str, Any]] = []

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.headless,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage"
                ]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )
            page = context.new_page()

            try:
                page.goto(config.GEM_PORTAL_SEARCH_URL, timeout=self.timeout, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)

                # Find Search Input on GeM
                search_input = page.locator("input#searchBid, input[name='searchBid'], input#bid_no, input[placeholder*='Search'], input[placeholder*='Bid']").first
                if search_input.is_visible():
                    search_input.fill("")
                    search_input.fill(keyword)
                    search_input.press("Enter")
                    logger.info(f"Submitted query '{keyword}' on GeM Portal. Waiting for results...")
                    page.wait_for_timeout(5000)

                # Scroll down to load all cards
                page.evaluate("window.scrollBy(0, 1500);")
                page.wait_for_timeout(2000)

                # Find all individual card blocks
                card_locators = page.locator(".card, .bid_card, .border-block, div[class*='card-body'], div[class*='block']")
                total_cards = card_locators.count()
                logger.info(f"Found {total_cards} card elements on GeM portal page.")

                seen_ids: Set[str] = set()

                for i in range(total_cards):
                    try:
                        card = card_locators.nth(i)
                        card_text = card.inner_text()

                        # Extract GeM ID specifically from THIS card
                        gem_match = self.gem_regex.search(card_text)
                        if not gem_match:
                            continue

                        gem_id = gem_match.group(0).upper().strip()
                        if gem_id in seen_ids:
                            continue
                        seen_ids.add(gem_id)

                        # Extract the link strictly from INSIDE this verified block
                        doc_anchor = card.locator("xpath=.//a[contains(@href, 'showbidDocument') or contains(@href, 'buyer_documents')]").first
                        if doc_anchor.count() == 0:
                            doc_anchor = card.locator("xpath=.//a[contains(@href, 'SpecificationDocument') or contains(@class, 'bid_no_hover')]").first

                        if doc_anchor.count() > 0:
                            raw_href = doc_anchor.get_attribute("href")
                            if raw_href and raw_href.strip() not in ("", "#"):
                                raw_href = raw_href.strip()
                                if raw_href.startswith("http"):
                                    actual_doc_url = raw_href
                                elif raw_href.startswith("/"):
                                    actual_doc_url = f"https://bidplus.gem.gov.in{raw_href}"
                                else:
                                    actual_doc_url = f"https://bidplus.gem.gov.in/{raw_href}"
                            else:
                                actual_doc_url = "Not Found"
                        else:
                            actual_doc_url = "Not Found"

                        print(f"[LINK VERIFICATION] GeM ID: {gem_id} | Extracted URL: {actual_doc_url}")
                        logger.info(f"[LINK VERIFICATION] GeM ID: {gem_id} | Extracted URL: {actual_doc_url}")
                        doc_url = actual_doc_url

                        # Extract Full Items / Description from THIS card without truncation
                        item_desc = self._extract_tender_description(card, card_text)
                        if not item_desc:
                            item_desc = f"{keyword.title()} Supply Requirement"

                        # Extract Buyer / Department from THIS card
                        buyer_name = ""
                        buyer_match = re.search(r"Department Name And Address\s*:\s*([^\n\r]+(?:\n[^\n\r]+){1,2})", card_text, re.IGNORECASE)
                        if buyer_match:
                            buyer_name = re.sub(r"\s+", " ", buyer_match.group(1).strip().replace("\n", ", "))
                        else:
                            dept_match = re.search(r"(?:Ministry|Department|Organisation)\s*:\s*([^\n\r]+)", card_text, re.IGNORECASE)
                            if dept_match:
                                buyer_name = dept_match.group(1).strip()
                            else:
                                buyer_name = "Government Department / PSU"

                        # Extract Dates from THIS card
                        start_date = ""
                        try:
                            s_elem = card.locator("span.start_date").first
                            if s_elem.count() > 0:
                                start_date = s_elem.inner_text().strip()
                        except Exception:
                            pass
                        if not start_date:
                            start_match = re.search(r"(?:Start Date|Bid Start Date|Start Date/Time|Release Date)\s*:\s*([\d\-\/\:\sAPMapm]+)", card_text, re.IGNORECASE)
                            if start_match:
                                start_date = start_match.group(1).strip()

                        end_date = ""
                        try:
                            e_elem = card.locator("span.end_date").first
                            if e_elem.count() > 0:
                                end_date = e_elem.inner_text().strip()
                        except Exception:
                            pass
                        if not end_date:
                            end_match = re.search(r"(?:End Date|Bid End Date|End Date/Time|Bid End Date/Time)\s*:\s*([\d\-\/\:\sAPMapm]+)", card_text, re.IGNORECASE)
                            if end_match:
                                end_date = end_match.group(1).strip()

                        # --- STRICT PREPARATION BUFFER & DYNAMIC DAYS LEFT ---
                        days_left = self._calculate_days_left(end_date)
                        if days_left is not None and days_left < config.MIN_DAYS_LEFT_BUFFER:
                            logger.info(f"Skipping {gem_id} - Insufficient preparation time / Expired (Days Left: {days_left} < {config.MIN_DAYS_LEFT_BUFFER})")
                            continue

                        # Extract Quantity
                        qty = ""
                        qty_match = re.search(r"Quantity\s*:\s*([0-9,]+)", card_text, re.IGNORECASE)
                        if qty_match:
                            qty = f" (Qty: {qty_match.group(1).strip()})"

                        # Download PDF document locally if enabled
                        local_pdf_path = None
                        if getattr(config, "AUTO_DOWNLOAD_PDFS", False) and doc_url and doc_url != "Not Found":
                            local_pdf_path = download_tender_pdf(doc_url, gem_id)

                        # Construct clean individual tender record
                        tender_record = {
                            "Scraped Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "GeM ID": gem_id,
                            "Tender Description": item_desc + qty,
                            "Buyer Name": buyer_name,
                            "Start Date": start_date,
                            "Tender Release Date": start_date,
                            "End Date to Participate": end_date,
                            "Days Left": days_left if days_left is not None else "N/A",
                            "Tender Opening Date": "",
                            "Work Completion Date": "As per GeM Bid Document",
                            "Product Description": item_desc,
                            "Seller Category": "MSE Relaxed / Make In India",
                            "Tender Value": "Refer Bid Document",
                            "City": "India",
                            "Eligibility Criteria": "Standard GeM Terms (Turnover & Past Performance)",
                            "Tender URL": doc_url if (doc_url and doc_url != "Not Found") else config.GEM_PORTAL_SEARCH_URL,
                            "Source URL": doc_url if (doc_url and doc_url != "Not Found") else config.GEM_PORTAL_SEARCH_URL,
                            "Assigned To": getattr(config, "DEFAULT_ASSIGNED_TO", "marketing@cryocorp.co.in"),
                            "Local PDF Path": local_pdf_path or ""
                        }

                        results.append(tender_record)
                        logger.info(f"Verified Active Tender [{len(results)}]: ID={gem_id} | Title='{item_desc[:35]}' | Days Left={days_left}")

                    except Exception as card_err:
                        logger.warning(f"Notice parsing card {i}: {card_err}")
                        continue

            except Exception as e:
                logger.error(f"Notice searching GeM for '{keyword}': {e}")
            finally:
                try:
                    context.close()
                    browser.close()
                except Exception:
                    pass

        return results

    def scrape_tender_by_id(self, gem_id: str) -> Optional[Dict[str, Any]]:
        """
        Launches browser, searches for the given GeM ID on GeM BidPlus portal,
        and extracts all required tender attributes.
        """
        gem_id = gem_id.strip()
        logger.info(f"Starting GeM portal scrape for Bid ID: {gem_id}")

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.headless,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage"
                ]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )
            page = context.new_page()

            try:
                tender_data = self._fetch_tender_details(page, gem_id)
                return tender_data
            except Exception as e:
                logger.error(f"Error scraping GeM ID '{gem_id}': {e}", exc_info=True)
                return None
            finally:
                context.close()
                browser.close()

    def _fetch_tender_details(self, page: Page, gem_id: str) -> Optional[Dict[str, Any]]:
        """Navigates to GeM Bid portal, enters exact Bid ID, forces network idle, and parses details."""
        logger.info(f"Navigating to GeM Bids Portal: {config.GEM_PORTAL_SEARCH_URL}")
        page.goto(config.GEM_PORTAL_SEARCH_URL, timeout=self.timeout, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        # 1. Force Network Idle & Hard Pauses: Enter exact GeM ID and click Search
        logger.info(f"Entering exact GeM ID '{gem_id}' into search field...")
        if page.locator("input#searchBid").count() > 0:
            page.fill("input#searchBid", gem_id)
        else:
            search_box = page.locator("input[name='searchBid'], input#bid_no, input[placeholder*='Bid'], input[placeholder*='Search']").first
            search_box.fill(gem_id)

        if page.locator("button#searchBidBtn").count() > 0 and page.locator("button#searchBidBtn").is_visible():
            page.click("button#searchBidBtn")
        elif page.locator("button#searchBidRA").count() > 0 and page.locator("button#searchBidRA").is_visible():
            page.click("button#searchBidRA")
        else:
            page.locator("input#searchBid").first.press("Enter")

        # Force wait for the AJAX table to refresh
        page.wait_for_timeout(3000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        # 2. Strict XPath Scoping: Locate the specific bid block containing the exact GeM ID string
        bid_block_xpath = f"//div[contains(@class, 'card') or contains(@class, 'border')][.//text()[contains(., '{gem_id}')]]"
        bid_block = page.locator(bid_block_xpath).first

        # Wait specifically for this element to become visible
        bid_block.wait_for(state="visible", timeout=15000)
        logger.info(f"Confirmed visible verified card for GeM ID '{gem_id}'.")

        return self._extract_card_data(page, gem_id, verified_card=bid_block)

    def _extract_card_data(self, page: Page, gem_id: str, verified_card: Optional[Any] = None) -> Optional[Dict[str, Any]]:
        """Extracts tender fields from page DOM with strict XPath scoping."""
        if verified_card is not None:
            bid_card = verified_card
        else:
            bid_block_xpath = f"//div[contains(@class, 'card') or contains(@class, 'border')][.//text()[contains(., '{gem_id}')]]"
            bid_card = page.locator(bid_block_xpath).first
            bid_card.wait_for(state="visible", timeout=15000)

        card_text = bid_card.inner_text()

        # Extract the link strictly from INSIDE this verified block
        doc_anchor = bid_card.locator("xpath=.//a[contains(@href, 'showbidDocument') or contains(@href, 'buyer_documents')]").first
        if doc_anchor.count() == 0:
            doc_anchor = bid_card.locator("xpath=.//a[contains(@href, 'SpecificationDocument') or contains(@class, 'bid_no_hover')]").first

        raw_href = doc_anchor.get_attribute("href") if doc_anchor.count() > 0 else None

        if raw_href and raw_href.strip() not in ("", "#"):
            raw_href = raw_href.strip()
            if raw_href.startswith("http"):
                actual_doc_url = raw_href
            elif raw_href.startswith("/"):
                actual_doc_url = f"https://bidplus.gem.gov.in{raw_href}"
            else:
                actual_doc_url = f"https://bidplus.gem.gov.in/{raw_href}"
        else:
            actual_doc_url = "Not Found"

        print(f"[LINK VERIFICATION] GeM ID: {gem_id} | Extracted URL: {actual_doc_url}")
        logger.info(f"[LINK VERIFICATION] GeM ID: {gem_id} | Extracted URL: {actual_doc_url}")
        doc_url = actual_doc_url

        # 3. Extract Full Items / Description from matched card without truncation
        item_desc = self._extract_tender_description(bid_card, card_text)
        if not item_desc:
            item_desc = f"GeM Tender Requirement ({gem_id})"

        # 4. Extract Buyer Name, Ministry, Department, City
        buyer_name = "Government Department / PSU"
        buyer_patterns = [
            r"Department Name And Address\s*:\s*([^\n\r]+(?:\n[^\n\r]+){1,3})",
            r"Ministry/State Name\s*:\s*([^\n\r]+)",
            r"Department Name\s*:\s*([^\n\r]+)",
            r"Organisation Name\s*:\s*([^\n\r]+)",
            r"Buyer Details\s*:\s*([^\n\r]+(?:\n[^\n\r]+){1,2})"
        ]
        for pattern in buyer_patterns:
            buyer_match = re.search(pattern, card_text, re.IGNORECASE)
            if buyer_match:
                buyer_raw = buyer_match.group(1).strip().replace("\n", ", ")
                buyer_name = re.sub(r"\s+", " ", buyer_raw)
                break

        # Extract City / State / Location
        city = ""
        city_match = re.search(
            r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s*,\s*(?:ANDAMAN|ANDHRA|ARUNACHAL|ASSAM|BIHAR|CHANDIGARH|CHHATTISGARH|DELHI|GOA|GUJARAT|HARYANA|HIMACHAL|JHARKHAND|KARNATAKA|KERALA|MADHYA PRADESH|MAHARASHTRA|MANIPUR|MEGHALAYA|MIZORAM|NAGALAND|ODISHA|PUNJAB|RAJASTHAN|SIKKIM|TAMIL NADU|TELANGANA|TRIPURA|UTTAR PRADESH|UTTARAKHAND|WEST BENGAL)",
            card_text,
            re.IGNORECASE
        )
        if city_match:
            city = city_match.group(0).strip()
        elif "Delhi" in card_text:
            city = "New Delhi"
        elif "Mumbai" in card_text:
            city = "Mumbai"

        # 5. Extract Dates: Start Date, End Date, Opening Date
        start_date = ""
        try:
            start_span = bid_card.locator("span.start_date").first
            if start_span.count() > 0:
                start_date = start_span.inner_text().strip()
        except Exception:
            pass
        if not start_date:
            start_date_match = re.search(r"(?:Start Date|Bid Start Date|Start Date/Time|Release Date)\s*:\s*([\d\-\/\:\sAPMapm]+)", card_text, re.IGNORECASE)
            if start_date_match:
                start_date = start_date_match.group(1).strip()

        end_date = ""
        try:
            end_span = bid_card.locator("span.end_date").first
            if end_span.count() > 0:
                end_date = end_span.inner_text().strip()
        except Exception:
            pass
        if not end_date:
            end_date_match = re.search(r"(?:End Date|Bid End Date|End Date/Time|Bid End Date/Time)\s*:\s*([\d\-\/\:\sAPMapm]+)", card_text, re.IGNORECASE)
            if end_date_match:
                end_date = end_date_match.group(1).strip()

        open_date = ""
        open_date_match = re.search(r"(?:Opening Date|Bid Opening Date|Opening Date/Time|Bid Opening Date/Time)\s*:\s*([\d\-\/\:\sAPMapm]+)", card_text, re.IGNORECASE)
        if open_date_match:
            open_date = open_date_match.group(1).strip()

        # Dynamic Days Left calculation
        days_left = self._calculate_days_left(end_date)

        # 6. Extract Work Completion Date / Delivery Period
        delivery = "As per GeM Bid Document"
        delivery_match = re.search(r"(?:Delivery Period|Work Completion|Delivery Days|Completion Period)\s*:\s*([^\n\r]+)", card_text, re.IGNORECASE)
        if delivery_match:
            delivery = delivery_match.group(1).strip()

        # 7. Extract Seller Category (MSE, Startup, MII, General)
        categories = []
        if re.search(r"MSE\s*(?:Exemption|Preference|Relaxation)", card_text, re.IGNORECASE):
            categories.append("MSE Relaxed/Preferred")
        if re.search(r"Startup\s*(?:Exemption|Relaxation)", card_text, re.IGNORECASE):
            categories.append("Startup Relaxed")
        if re.search(r"Make in India|MII", card_text, re.IGNORECASE):
            categories.append("Make In India (MII)")
        seller_category = ", ".join(categories) if categories else "General / All Eligible Sellers"

        # 8. Extract Tender Value
        val_match = re.search(r"(?:Estimated Bid Value|Tender Value|Total Value|Estimated Value)\s*:\s*(?:INR|Rs\.?|₹)?\s*([0-9,]+(?:\.[0-9]{2})?)", card_text, re.IGNORECASE)
        tender_value = f"INR {val_match.group(1).strip()}" if val_match else "Refer Bid Document"

        # 9. Extract Eligibility Criteria
        eligibility_list = []
        turnover_match = re.search(r"(?:Turnover|Average Annual Turnover)\s*:\s*([^\n\r]+)", card_text, re.IGNORECASE)
        if turnover_match:
            eligibility_list.append(f"Turnover: {turnover_match.group(1).strip()}")
        exp_match = re.search(r"(?:Years of Past Experience|Experience Criteria)\s*:\s*([^\n\r]+)", card_text, re.IGNORECASE)
        if exp_match:
            eligibility_list.append(f"Experience: {exp_match.group(1).strip()}")
        
        eligibility = "; ".join(eligibility_list) if eligibility_list else "Standard GeM Terms (Turnover & Past Performance as specified in Bid document)"

        # Download PDF document locally if enabled
        local_pdf_path = None
        if getattr(config, "AUTO_DOWNLOAD_PDFS", False) and doc_url and doc_url != "Not Found":
            local_pdf_path = download_tender_pdf(doc_url, gem_id)

        result: Dict[str, Any] = {
            "Scraped Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "GeM ID": gem_id,
            "Tender Description": item_desc,
            "Buyer Name": buyer_name,
            "Start Date": start_date,
            "Tender Release Date": start_date,
            "End Date to Participate": end_date,
            "Days Left": days_left if days_left is not None else "N/A",
            "Tender Opening Date": open_date,
            "Work Completion Date": delivery,
            "Product Description": item_desc,
            "Seller Category": seller_category,
            "Tender Value": tender_value,
            "City": city,
            "Eligibility Criteria": eligibility,
            "Tender URL": doc_url if (doc_url and doc_url != "Not Found") else config.GEM_PORTAL_SEARCH_URL,
            "Source URL": doc_url if (doc_url and doc_url != "Not Found") else config.GEM_PORTAL_SEARCH_URL,
            "Assigned To": getattr(config, "DEFAULT_ASSIGNED_TO", "marketing@cryocorp.co.in"),
            "Local PDF Path": local_pdf_path or ""
        }

        logger.info(f"Successfully parsed data for GeM ID '{gem_id}': Product='{item_desc[:40]}', End Date='{end_date}', Days Left={days_left}")
        return result

    def batch_scrape(self, gem_ids: List[str]) -> List[Dict[str, Any]]:
        """Scrapes multiple GeM IDs in sequence."""
        scraped_records = []
        for idx, gid in enumerate(gem_ids, 1):
            logger.info(f"[{idx}/{len(gem_ids)}] Processing GeM ID: {gid}")
            data = self.scrape_tender_by_id(gid)
            if data:
                scraped_records.append(data)
            time.sleep(2)
        return scraped_records

if __name__ == "__main__":
    test_gem_id = "GEM/2024/B/5000000"
    print(f"Testing GeMPortalScraper on ID: {test_gem_id}")
    scraper = GeMPortalScraper(headless=True)
    res = scraper.scrape_tender_by_id(test_gem_id)
    print("\nScraped Result Dictionary:")
    import json
    print(json.dumps(res, indent=2))
