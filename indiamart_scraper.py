import re
import time
import urllib.parse
import logging
from typing import List, Dict, Set, Any
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def is_relevant_tender(title: str, description: str) -> bool:
    """
    Validates whether the title or description matches any keyword in TARGET_KEYWORDS whitelist.
    """
    combined_text = f"{title} {description}".lower()
    return any(keyword.lower() in combined_text for keyword in config.TARGET_KEYWORDS)

class IndiaMARTScraper:
    """
    Custom Playwright browser automation scraper for IndiaMART tender & product inquiries.
    Searches for industrial keywords and strictly scans relevant listing cards for GeM IDs.
    """

    def __init__(self, headless: bool = config.HEADLESS, timeout: int = config.BROWSER_TIMEOUT_MS):
        self.headless = headless
        self.timeout = timeout
        self.gem_regex = re.compile(config.GEM_ID_REGEX, re.IGNORECASE)

    def search_keyword(self, keyword: str, max_pages: int = 2) -> Dict[str, Any]:
        """
        Searches IndiaMART for a specific keyword and extracts validated GeM IDs and card text.
        Applies strict keyword filtering so only relevant tender cards are captured.
        """
        encoded_query = urllib.parse.quote_plus(keyword)
        search_url = f"https://dir.indiamart.com/search.mp?ss={encoded_query}"
        logger.info(f"Searching IndiaMART for keyword '{keyword}' at: {search_url}")

        found_gem_ids: Set[str] = set()
        matched_items: List[Dict[str, Any]] = []

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
                for current_page in range(1, max_pages + 1):
                    target_url = f"{search_url}&page={current_page}" if current_page > 1 else search_url
                    logger.info(f"Navigating to page {current_page}: {target_url}")
                    page.goto(target_url, timeout=self.timeout, wait_until="domcontentloaded")
                    page.wait_for_timeout(3000)

                    # Scroll down to trigger lazy loading of search listing cards
                    page.evaluate("window.scrollBy(0, document.body.scrollHeight / 2);")
                    page.wait_for_timeout(1500)
                    page.evaluate("window.scrollBy(0, document.body.scrollHeight);")
                    page.wait_for_timeout(1500)

                    # Inspect individual product/tender card blocks for detailed context
                    card_locators = page.locator(".card, .product-card, .listing-card, .m-card, div[class*='listing']")
                    count = card_locators.count()
                    logger.info(f"Found {count} listing blocks on page {current_page}")

                    for i in range(count):
                        try:
                            card_text = card_locators.nth(i).inner_text()
                            if not card_text:
                                continue

                            # Strict Whitelist Validation Gate
                            if not is_relevant_tender(keyword, card_text):
                                logger.debug(f"Discarding irrelevant IndiaMART card: does not match TARGET_KEYWORDS")
                                continue

                            card_gem_matches = self.gem_regex.findall(card_text)
                            for gid in card_gem_matches:
                                gid_clean = gid.upper().strip()
                                found_gem_ids.add(gid_clean)
                                matched_items.append({
                                    "gem_id": gid_clean,
                                    "keyword": keyword,
                                    "card_snippet": card_text[:200].replace("\n", " ")
                                })
                                logger.info(f"Found relevant GeM ID on IndiaMART: {gid_clean} (Keyword: '{keyword}')")
                        except Exception as card_err:
                            logger.debug(f"Notice parsing IndiaMART card {i}: {card_err}")
                            continue

                    # Check if there is a next page
                    next_button = page.locator("a[rel='next'], a:has-text('Next')").first
                    if not next_button.is_visible():
                        break

            except PlaywrightTimeoutError:
                logger.warning(f"Timeout while loading IndiaMART search for '{keyword}'")
            except Exception as e:
                if "closed" in str(e).lower():
                    logger.info("Browser window was closed.")
                else:
                    logger.error(f"Notice while scraping IndiaMART for '{keyword}': {e}")
            finally:
                try:
                    context.close()
                    browser.close()
                except Exception:
                    pass

        logger.info(f"Keyword '{keyword}' search completed. Discovered GeM IDs: {list(found_gem_ids)}")
        return {
            "keyword": keyword,
            "gem_ids": list(found_gem_ids),
            "items": matched_items
        }

    def scrape_all_keywords(self, keywords: List[str] = config.INDIAMART_KEYWORDS) -> List[str]:
        """
        Runs search across all target keywords and returns a deduplicated list of GeM IDs.
        """
        all_gem_ids: Set[str] = set()
        for kw in keywords:
            result = self.search_keyword(kw)
            all_gem_ids.update(result["gem_ids"])
            time.sleep(2)

        logger.info(f"Total unique GeM IDs discovered across {len(keywords)} keywords: {len(all_gem_ids)}")
        return list(all_gem_ids)

if __name__ == "__main__":
    scraper = IndiaMARTScraper(headless=True)
    results = scraper.search_keyword("cooling tower tender")
    print(f"\nDiscovered GeM IDs: {results['gem_ids']}")
