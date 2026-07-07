"""Tmall review collector — browser automation and review interception via Playwright.

Cross-platform: uses Playwright's bundled Chromium on Linux/Docker, and an
optional system Chrome/Edge channel on Windows for local debugging.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import BrowserContext, Page, Response, TimeoutError, sync_playwright

from review_scraper.core.database import get_session
from review_scraper.models import CrawlBatch, Product, Review, ScrapeTaskReview

logger = logging.getLogger(__name__)

from .parser import (
    extract_item_id as _extract_item_id,
    extract_page_number_from_url,
    flatten_review,
    locate_review_list,
    parse_jsonp,
    review_key,
)

LOGIN_HOSTS = {"login.taobao.com", "login.m.taobao.com"}
COMMENT_URL_MARKERS = (
    "list_detail_rate",
    "itemreview",
    "review",
    "rate.tmall.com",
    "mtop.taobao.rate",
    "mtop.taobao.review",
    "mtop.alibaba.review",
    "detaillist.get",
    "rate.detaillist",
    "review.list.for.new.pc.detail",
)
DEFAULT_TIMEOUT_MS = 30_000


@dataclass
class ReviewBatch:
    source_url: str
    page_number: int | None
    total_pages: int | None
    review_count: int
    payload: dict[str, Any]
    reviews: list[dict[str, Any]]
    fingerprint: str


class ReviewCollector:
    def __init__(self, scan_all_responses: bool = False, task_id: str | None = None) -> None:
        self._scan_all_responses = scan_all_responses
        self._task_id = task_id
        self._batches: list[ReviewBatch] = []
        self._fingerprints: set[str] = set()
        self._rows: list[dict[str, Any]] = []
        self._row_keys: set[tuple[str, str, str, str]] = set()
        self._db_keys: set[tuple[str, str]] = set()
        self._captured_pages: set[int] = set()
        self._batch_to_rows: dict[str, list[dict[str, Any]]] = {}
        self._persisted_fingerprints: set[str] = set()
        self.on_batch_captured: Callable[[ReviewBatch], None] | None = None

    @property
    def captured_pages(self) -> set[int]:
        return set(self._captured_pages)

    @property
    def latest_total_pages(self) -> int | None:
        for batch in reversed(self._batches):
            if batch.total_pages:
                return batch.total_pages
        return None

    @property
    def batch_count(self) -> int:
        return len(self._batches)

    @property
    def rows(self) -> list[dict[str, Any]]:
        return list(self._rows)

    @property
    def batches(self) -> list[ReviewBatch]:
        return list(self._batches)

    def maybe_add(self, response: Response) -> None:
        url = response.url
        should_probe = any(marker in url for marker in COMMENT_URL_MARKERS) or self._scan_all_responses
        if not should_probe:
            return

        content_type = response.headers.get("content-type", "")
        if "json" not in content_type and "javascript" not in content_type and "text/plain" not in content_type:
            return

        try:
            text = response.text()
            if len(text) > 2_500_000:
                return
            payload = parse_jsonp(text)
        except Exception:
            return

        if not isinstance(payload, dict):
            return

        self._ingest_payload(url, payload)

    def _ingest_payload(self, url: str, payload: dict[str, Any]) -> bool:
        """Extract reviews from a parsed mtop payload and add them to the
        collector's row/batch stores. Returns True iff any reviews were
        added. Shared by network-response capture and direct-API fallback.
        """
        reviews = locate_review_list(payload)
        if not reviews:
            return False

        page_number, total_pages = extract_pagination_info_from_dict(payload)
        if page_number is None:
            page_number = extract_page_number_from_url(url)

        fingerprint = json.dumps(reviews, ensure_ascii=False, sort_keys=True)
        if fingerprint in self._fingerprints:
            return False

        batch = ReviewBatch(
            source_url=url,
            page_number=page_number,
            total_pages=total_pages,
            review_count=len(reviews),
            payload=payload,
            reviews=reviews,
            fingerprint=fingerprint,
        )
        self._fingerprints.add(fingerprint)
        self._batches.append(batch)
        if batch.page_number is not None:
            self._captured_pages.add(batch.page_number)

        batch_rows: list[dict[str, Any]] = []
        for index, review in enumerate(reviews, start=1):
            row = flatten_review(review, batch=batch, index=index)
            key = review_key(row)
            if key in self._row_keys:
                continue
            self._row_keys.add(key)
            self._rows.append(row)
            batch_rows.append(row)
        self._batch_to_rows[fingerprint] = batch_rows

        if self.on_batch_captured is not None:
            try:
                self.on_batch_captured(batch)
            except Exception as cb_exc:
                logger.warning("on_batch_captured hook failed: %s", cb_exc)
        return True

    def persist_to_database(self, item_id: str, source_url: str | None = None, only_last_batch: bool = False) -> int:
        """Persist captured batches and reviews to the database.

        - Upserts the Product row.
        - Writes one CrawlBatch row per captured API response.
        - Writes deduplicated Review rows, keyed by the
          (platform, external_product_id, review_id) unique constraint.
        Returns the number of newly inserted reviews.

        When ``only_last_batch=True``, only the most recent batch (and its
        reviews) are written. Already-persisted batches are skipped via the
        in-memory ``_persisted_fingerprints`` set so this method is safe to
        call from the ``on_batch_captured`` hook for incremental persistence.
        """
        db = get_session()
        inserted = 0
        try:
            product = (
                db.query(Product)
                .filter(Product.external_product_id == item_id, Product.platform == "tmall")
                .first()
            )
            if product is None:
                product = Product(
                    platform="tmall",
                    source_url=source_url or "",
                    normalized_url=source_url or "",
                    external_product_id=item_id,
                    marketplace="tmall",
                )
                db.add(product)
                db.flush()
            else:
                if source_url and not product.source_url:
                    product.source_url = source_url
                if source_url and not product.normalized_url:
                    product.normalized_url = source_url

            if only_last_batch:
                target_batches = self._batches[-1:] if self._batches else []
            else:
                target_batches = self._batches

            # Persist every targeted batch (raw audit trail). We collect the
            # fingerprints into a pending set so we only mark them as
            # persisted after the commit succeeds.
            pending_fingerprints: set[str] = set()
            for batch in target_batches:
                if batch.fingerprint in self._persisted_fingerprints:
                    continue
                if batch.fingerprint in pending_fingerprints:
                    continue
                pending_fingerprints.add(batch.fingerprint)
                fp_hash = hashlib.sha256(batch.fingerprint.encode("utf-8")).hexdigest()[:64]
                db.add(
                    CrawlBatch(
                        task_id=self._task_id,
                        product_id=product.id,
                        platform="tmall",
                        source_url=batch.source_url,
                        page_number=batch.page_number,
                        total_pages=batch.total_pages,
                        review_count=batch.review_count,
                        fingerprint=fp_hash,
                        raw_payload=batch.payload,
                    )
                )

            if only_last_batch:
                # Only insert reviews that belong to the latest batch's fingerprint.
                latest_fp = self._batches[-1].fingerprint if self._batches else None
                rows_to_insert = self._batch_to_rows.get(latest_fp, []) if latest_fp else []
            else:
                rows_to_insert = self._rows

            # Track keys/fingerprints locally so we only commit them after a
            # successful flush+commit. If the transaction rolls back we should
            # still be able to retry these rows on the next call.
            pending_db_keys: set[tuple] = set()

            for row in rows_to_insert:
                db_key = (product.id, row.get("review_id", ""))
                if db_key in self._db_keys or db_key in pending_db_keys:
                    continue
                pending_db_keys.add(db_key)
                exists = (
                    db.query(Review)
                    .filter(
                        Review.product_id == product.id,
                        Review.review_id == row.get("review_id", ""),
                    )
                    .first()
                )
                if exists is not None:
                    review = exists
                else:
                    content_str = (row.get("content") or "").strip()
                    dedup_hash = hashlib.sha256(
                        f"tmall|{item_id}|{row.get('user_nick', '')}|{row.get('review_date', '')}|{content_str}".encode("utf-8")
                    ).hexdigest()

                    review = Review(
                        task_id=self._task_id,
                        product_id=product.id,
                        platform="tmall",
                        external_product_id=item_id,
                        review_id=row.get("review_id", ""),
                        user_nick=row.get("user_nick"),
                        review_date=row.get("review_date"),
                        sku=row.get("sku"),
                        content=row.get("content"),
                        append_content=row.get("append_content"),
                        append_date=row.get("append_date"),
                        helpful_count=int(row.get("helpful_count") or 0),
                        page_number=row.get("page_number"),
                        total_pages=row.get("total_pages"),
                        picture_urls=row.get("picture_urls"),
                        source_url=row.get("source_url") or source_url,
                        dedup_hash=dedup_hash,
                    )
                    db.add(review)
                    # Flush so that review.id is populated before we insert
                    # into the association table (review_id is NOT NULL).
                    db.flush()
                    inserted += 1

                if self._task_id is not None and review is not None:
                    existing_link = (
                        db.query(ScrapeTaskReview)
                        .filter(
                            ScrapeTaskReview.task_id == self._task_id,
                            ScrapeTaskReview.review_id == review.id,
                        )
                        .first()
                    )
                    if existing_link is None:
                        db.add(
                            ScrapeTaskReview(
                                task_id=self._task_id,
                                review_id=review.id,
                                product_id=product.id,
                            )
                        )

            db.commit()

            # Only after a successful commit do we promote the pending
            # markers to the persistent instance sets. A rollback above
            # means the next call can still try these rows.
            self._db_keys.update(pending_db_keys)
            self._persisted_fingerprints.update(pending_fingerprints)
            return inserted
        finally:
            db.close()


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def browser_executable(browser_name: str) -> str | None:
    """Resolve a local browser executable on Windows for local debugging.

    Returns None on Linux/Docker so Playwright falls back to its bundled
    Chromium. Only Chrome and Edge channels are supported — the API no longer
    exposes firefox/safari since the collector only works with chromium.
    """
    if not _is_windows():
        return None

    candidates = {
        "chrome": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ],
        "edge": [
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ],
    }
    for path in candidates.get(browser_name, []):
        if Path(path).exists():
            return path
    return None


def create_context(browser_name: str, profile_dir: Path, headless: bool) -> BrowserContext:
    profile_dir.mkdir(parents=True, exist_ok=True)
    playwright = sync_playwright().start()
    logger.info("create_context: browser=%s profile_dir=%s headless=%s", browser_name, profile_dir, headless)

    launch_kwargs: dict[str, Any] = {
        "headless": headless,
        "viewport": {"width": 1440, "height": 1200},
    }

    executable = browser_executable(browser_name)
    if executable:
        # Windows local debugging: use the installed Chrome/Edge.
        launch_kwargs["executable_path"] = executable
    # Linux/Docker: use Playwright's bundled Chromium (no executable_path).

    context = playwright.chromium.launch_persistent_context(str(profile_dir), **launch_kwargs)
    setattr(context, "_tmall_playwright", playwright)
    return context


def close_context(context: BrowserContext) -> None:
    playwright = getattr(context, "_tmall_playwright", None)
    try:
        context.close()
    finally:
        if playwright is not None:
            playwright.stop()


def is_login_page(page: Page) -> bool:
    host = urlparse(page.url).hostname or ""
    return host in LOGIN_HOSTS


def is_item_page(page: Page, item_id: str) -> bool:
    parsed = urlparse(page.url)
    host = (parsed.hostname or "").lower()
    if "detail.tmall.com" not in host and "detail.m.tmall.com" not in host:
        return False
    query_item_id = parse_qs(parsed.query).get("id", [""])[0]
    if query_item_id and query_item_id == item_id:
        return True
    return item_id in page.url


def relogin_via_headed_browser(
    browser: str,
    profile_path: Path,
    login_timeout_seconds: int,
) -> None:
    """Open a headed browser on the same profile dir and wait for manual login.

    The caller is responsible for closing the previous (typically headless)
    context before invoking this — both share the same ``--user-data-dir`` and
    cannot be open at the same time. When the user completes the Tmall login
    flow, the freshly minted cookies are persisted into the profile dir by
    Chromium itself, so the next ``create_context`` call (in the caller's
    preferred mode) will reuse them.

    Raises:
        TimeoutError: if the login flow is not completed within
            ``login_timeout_seconds``.
    """
    print(
        "[relogin] Saved cookies were rejected. Opening a visible browser "
        "so you can scan the QR code or enter your password."
    )
    context = create_context(browser, profile_path, headless=False)
    try:
        page = first_page(context)
        # Drive the user straight into the login flow by hitting a top-level
        # taobao URL. If the existing cookies were valid this would just
        # land on the logged-in homepage, but in the re-login path we know
        # they are not.
        try:
            page.goto("https://login.taobao.com/", wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
        except Exception:
            pass
        page.wait_for_timeout(2_000)

        deadline = time.time() + login_timeout_seconds
        while time.time() < deadline:
            if not is_login_page(page):
                # Give Chromium a moment to flush the freshly set cookies
                # to the SQLite store before we close the context.
                page.wait_for_timeout(3_000)
                print("[relogin] Login detected, closing headed browser and resuming the task.")
                return
            page.wait_for_timeout(1_500)

        raise TimeoutError(
            f"[relogin] Timed out after {login_timeout_seconds}s waiting for "
            f"manual login. Current URL: {page.url}"
        )
    finally:
        close_context(context)


def ensure_item_page(
    page: Page,
    item_url: str,
    item_id: str,
    login_timeout_seconds: int,
) -> bool:
    """Navigate to the item page and wait until we are on it.

    Returns:
        True if the page ends up on the item page, False if Tmall redirected
        us to a login page (cookies were rejected). The caller is expected
        to handle the False case by tearing down the current context and
        running ``relogin_via_headed_browser``.

    Raises:
        TimeoutError: if neither the item page nor the login page is reached
            within ``login_timeout_seconds``.
    """
    deadline = time.time() + login_timeout_seconds
    announced_login = False

    while time.time() < deadline:
        try:
            page.goto(item_url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
        except Exception as exc:
            logger.warning("page.goto failed (%s), retrying", exc)
            page.wait_for_timeout(1_000)
            continue
        page.wait_for_timeout(2_000)

        if is_item_page(page, item_id):
            page.wait_for_timeout(2_000)
            return True

        if is_login_page(page):
            if not announced_login:
                print(
                    "[auth] Tmall redirected to login — saved cookies were "
                    "rejected. Signalling caller to re-authenticate."
                )
                announced_login = True
            return False

        page.wait_for_timeout(1_000)

    raise TimeoutError(
        "Timed out while waiting for item page readiness. "
        f"Current URL: {page.url}"
    )


def first_page(context: BrowserContext) -> Page:
    if context.pages:
        return context.pages[0]
    return context.new_page()


def open_review_section(page: Page, sort_mode: str) -> None:
    """Click the review tab on the item page and wait for the review API to fire.

    Tmall ships several layouts for the detail page; we try multiple click
    strategies in order of preference so the same code works across layouts.

    Order of attempts:
        1. Wait for the 2025 layout's bottomSwitchTabsWrap container to be
           populated by React, then click the item whose text contains
           "用户评价" / "评价" / "评论".
        2. Fall back to legacy selectors: visible text matching "评价" / "评论"
           / "全部评价" anywhere on the page.
        3. Scroll the page to provoke lazy loaders.
    """
    clicked = False

    # 1. 2025 layout: bottomSwitchTabsWrap tab list (React-hydrated).
    #    Poll up to 12s for the wrapper to be populated, then click the
    #    matching tab. The CSS-module suffix (e.g. "CEKqvt02") changes per
    #    build, so we match on a stable substring.
    for _ in range(24):
        try:
            tab_text = page.evaluate(
                """
                () => {
                  const wraps = document.querySelectorAll('[class*="bottomSwitchTabsWrap"]');
                  for (const w of wraps) {
                    const items = w.querySelectorAll('[class*="switchTabsItem"]');
                    for (const it of items) {
                      const t = (it.innerText || '').trim();
                      if (t) return t;
                    }
                  }
                  return null;
                }
                """
            )
            if tab_text:
                # Click the tab whose text contains a review-y keyword.
                page.evaluate(
                    """
                    () => {
                      const wraps = document.querySelectorAll('[class*="bottomSwitchTabsWrap"]');
                      for (const w of wraps) {
                        const items = w.querySelectorAll('[class*="switchTabsItem"]');
                        for (const it of items) {
                          const t = (it.innerText || '').trim();
                          if (/(评价|评论|comment|rate|review)/i.test(t)) {
                            it.click();
                            return true;
                          }
                        }
                      }
                      return false;
                    }
                    """
                )
                page.wait_for_timeout(2_500)
                clicked = True
                break
        except Exception as exc:
            logger.warning("bottomSwitchTabsWrap tab probe failed: %s", exc)
        page.wait_for_timeout(500)

    if not clicked:
        # 2. Legacy layout: visible text search.
        labels = [
            re.compile(r"用户评价"),
            re.compile(r"(?:累计)?评价"),
            re.compile(r"评论"),
            re.compile(r"全部评价"),
        ]
        for label in labels:
            try:
                loc = page.get_by_text(label).first
                if loc.is_visible():
                    loc.click(timeout=2_000)
                    page.wait_for_timeout(2_500)
                    clicked = True
                    break
            except Exception:
                continue

    if not clicked:
        # 3. Last resort: scroll the page and try one more time. Some
        #    lazy-loaders only mount the review tab after the user scrolls
        #    the image gallery past a certain threshold.
        try:
            for offset in (1000, 2000, 3000, 4500):
                page.mouse.wheel(0, offset)
                page.wait_for_timeout(800)
            page.evaluate("() => window.scrollTo(0, 0)")
            page.wait_for_timeout(1_500)
            for label in (re.compile(r"用户评价"), re.compile(r"评价"), re.compile(r"评论")):
                try:
                    loc = page.get_by_text(label).first
                    if loc.is_visible():
                        loc.click(timeout=2_000)
                        page.wait_for_timeout(2_500)
                        break
                except Exception as exc:
                    logger.debug("scroll-rescue click failed for %r: %s", label.pattern, exc)
                    continue
        except Exception as exc:
            logger.warning("scroll-rescue open_review_section failed: %s", exc)

    # Always scroll a little so the review list is visible in the viewport.
    try:
        page.mouse.wheel(0, 1500)
        page.wait_for_timeout(1_000)
    except Exception as exc:
        logger.warning("viewport scroll failed: %s", exc)

    if sort_mode == "latest":
        sort_candidates = [
            page.get_by_text(re.compile(r"最新")),
            page.get_by_text(re.compile(r"时间")),
        ]
        for locator in sort_candidates:
            try:
                if locator.first.is_visible():
                    locator.first.click(timeout=2_000)
                    page.wait_for_timeout(2_000)
                    break
            except Exception:
                continue


def wait_for_new_batch(page: Page, collector: ReviewCollector, previous_count: int, timeout_ms: int) -> bool:
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        if collector.batch_count > previous_count:
            return True
        page.wait_for_timeout(500)
    return collector.batch_count > previous_count


def _maybe_fill_gap(
    page: Page,
    collector: ReviewCollector,
    total_pages: int | None,
    max_pages: int,
    max_retries_per_missing: int = 2,
) -> int:
    """Try to fill missing pages by clicking the next-page button.

    Returns the number of new batches ingested by this call.
    """
    if total_pages is None or total_pages <= 1:
        return 0
    if max_pages and collector.batch_count >= max_pages:
        return 0

    added = 0
    attempts = 0
    while (
        len(collector.captured_pages) < total_pages
        and attempts < max_retries_per_missing
        and (not max_pages or collector.batch_count < max_pages)
    ):
        if not click_next_page(page):
            logger.info("fill-gap: click_next_page returned False at attempt %d", attempts)
            break
        previous = collector.batch_count
        if not wait_for_new_batch(page, collector, previous_count=previous, timeout_ms=10_000):
            logger.info("fill-gap: no new batch within 10s at attempt %d", attempts)
            break
        added += collector.batch_count - previous
        attempts += 1
    return added


def _fill_gap_via_api(
    page: Page,
    item_id: str,
    sort: str,
    collector: ReviewCollector,
    candidate: dict[str, Any],
    total_pages: int,
    max_pages: int,
) -> int:
    """Fallback-path gap fill: directly call the mtop API for missing pages.

    Reuses the same envelope-unwrap + parse pattern as ``_fetch_reviews_via_page``.
    """
    api = candidate["api"]
    url_template = candidate["url_template"]
    data_tmpl = candidate["data_template"]
    extra_params: dict[str, Any] = {}
    if sort == "latest":
        extra_params["sort"] = "latest"
        extra_params["order"] = "date_desc"

    added = 0
    for p in range(1, total_pages + 1):
        if p in collector.captured_pages:
            continue
        if max_pages and collector.batch_count >= max_pages:
            break
        payload = {
            "api": api,
            "v": candidate["v"],
            "data": {**data_tmpl(item_id, p), **extra_params},
            "timeout": 20000,
            "ttid": "2022@taobao_litepc_9.17.0",
            "AntiFlood": True,
            "AntiCreep": True,
            "jsonpIncPrefix": "scrape",
        }
        try:
            result = page.evaluate(
                """async (req) => {
                    try {
                        const r = await window.lib.mtop.request(req);
                        if (r && typeof r === 'object' && (r.retType === -1 || r.errorCode)) {
                            return { ok: false, error: JSON.stringify({retType: r.retType, errorCode: r.errorCode, ret: r.ret}) };
                        }
                        return { ok: true, data: r };
                    } catch (e) {
                        return { ok: false, error: (e && e.message) ? String(e.message) : JSON.stringify(e) };
                    }
                }""",
                payload,
            )
        except Exception as exc:
            logger.warning("fill-gap-api: page %d threw %r, stopping gap fill", p, exc)
            break

        if not isinstance(result, dict) or not result.get("ok"):
            logger.info("fill-gap-api: page %d failed: %s", p, (result or {}).get("error") if isinstance(result, dict) else "no response")
            break

        raw = result.get("data")
        if isinstance(raw, str):
            try:
                envelope = json.loads(raw)
            except Exception:
                envelope = {}
        elif isinstance(raw, dict):
            envelope = raw
        else:
            envelope = {}

        page_data: Any = envelope
        for _ in range(3):
            if not isinstance(page_data, dict):
                page_data = {}
                break
            inner = page_data.get("data")
            if isinstance(inner, str):
                try:
                    page_data = json.loads(inner)
                except Exception:
                    break
            elif isinstance(inner, dict):
                page_data = inner
            else:
                break

        if not _looks_like_review_payload(page_data):
            break

        before = collector.batch_count
        if collector._ingest_payload(url_template, page_data):
            added += collector.batch_count - before
    return added


def is_disabled(locator: Any) -> bool:
    try:
        classes = locator.get_attribute("class") or ""
        disabled = locator.get_attribute("disabled")
        aria_disabled = locator.get_attribute("aria-disabled")
        return (
            "disabled" in classes.lower()
            or disabled is not None
            or aria_disabled == "true"
        )
    except Exception:
        return False


def click_next_page(page: Page) -> bool:
    selectors = [
        page.get_by_text(re.compile(r"下一页|下页")),
        page.locator("li.next, .next, [class*='next']"),
        page.locator("button[aria-label*='next'], a[aria-label*='next']"),
    ]

    for locator in selectors:
        try:
            count = min(locator.count(), 5)
        except Exception:
            continue

        for index in range(count):
            candidate = locator.nth(index)
            try:
                if not candidate.is_visible() or is_disabled(candidate):
                    continue
                candidate.click(timeout=3_000)
                page.wait_for_timeout(2_500)
                return True
            except Exception:
                continue
    return False


def extract_pagination_info_from_dict(payload: dict[str, Any]) -> tuple[int | None, int | None]:
    from .parser import extract_pagination_info
    return extract_pagination_info(payload)


# Mtop API endpoints + versions used by Tmall's pc-detail-ssr-2025 layout.
# Either may be tried; whichever the current page's window.lib.mtop routes to.
_REVIEW_API_CANDIDATES = (
    {
        "api": "mtop.alibaba.review.list.for.new.pc.detail",
        "v": "1.0",
        # New layout: pageNum-based pagination; channel is "detail".
        "data_template": lambda item_id, page_num: {
            "auctionNumId": item_id,
            "pageSize": 20,
            "pageNo": page_num,
            "channel": "detail",
        },
        "url_template": "https://h5api.m.tmall.com/h5/mtop.alibaba.review.list.for.new.pc.detail/1.0/",
    },
    {
        "api": "mtop.taobao.rate.detaillist.get",
        "v": "6.0",
        # Legacy layout: pageNo-based pagination; sellerId is optional.
        "data_template": lambda item_id, page_num: {
            "auctionNumId": item_id,
            "pageNo": page_num,
            "pageSize": 20,
        },
        "url_template": "https://h5api.m.tmall.com/h5/mtop.taobao.rate.detaillist.get/6.0/",
    },
)


def _fetch_reviews_via_page(
    page: Page,
    item_id: str,
    sort: str,
    collector: ReviewCollector,
    max_pages: int = 0,
) -> int:
    """Drive the browser's already-loaded mtop SDK to call the review API
    directly, bypassing the on-page review-tab UI.

    The Tmall pc-detail-ssr-2025 layout lazy-loads the bottomSwitchTabsWrap
    React component only after specific user interaction; in headless mode
    the tab often never opens, so no review API request is fired and the
    network listener never captures anything. The page does, however,
    always expose ``window.lib.mtop`` (the SDK bundle that the page itself
    uses), which already carries the right cookies, the ``_m_h5_tk`` token
    for the sign parameter, and the bx-anti-flood params. We just call
    ``window.lib.mtop.request(...)`` for each page and feed the parsed
    payload into the collector via ``_ingest_payload``.

    Returns the number of payloads ingested (0 if every attempt failed).
    Raises:
        RuntimeError: if ``window.lib.mtop`` is not available, or every
            API candidate returned a top-level failure (e.g. login
            required).
    """
    page_count = 0
    added = 0

    # Pin the sort option. New API takes "sort" as "overall"/"latest";
    # legacy API takes "order". We pass both; the SDK strips unused keys.
    extra_params: dict[str, Any] = {}
    if sort == "latest":
        extra_params["sort"] = "latest"
        extra_params["order"] = "date_desc"

    has_sdk = page.evaluate("() => typeof window.lib !== 'undefined' && !!window.lib.mtop")
    if not has_sdk:
        raise RuntimeError(
            "window.lib.mtop is not available on the page — cannot call "
            "the review API directly. Make sure the page has finished "
            "loading the Tmall SDK before running the scrape."
        )

    last_errors: list[str] = []
    for candidate in _REVIEW_API_CANDIDATES:
        api = candidate["api"]
        v = candidate["v"]
        data_tmpl = candidate["data_template"]
        url_template = candidate["url_template"]

        try:
            # Probe the first page first; bail early if the API is
            # disabled / removed by Tmall. We use pageNumber=1 always —
            # pagination comes from the server.
            #
            # The mtop SDK validates {type, dataType} strictly and routes
            # to one of four request branches (getJSONP / getOriginalJSONP
            # / getJSON / postJSON). Anything else hits
            # UNEXCEPT_REQUEST::错误的请求类型. Real Tmall wrappers
            # rely on the SDK defaults — type=get, dataType=jsonp — and
            # let the SDK auto-set H5Request=true. Mirror that exactly so
            # the SDK routes through __requestJSONP, which works.
            payload = {
                "api": api,
                "v": v,
                "data": {**data_tmpl(item_id, 1), **extra_params},
                "timeout": 20000,
                "ttid": "2022@taobao_litepc_9.17.0",
                "AntiFlood": True,
                "AntiCreep": True,
                "jsonpIncPrefix": "scrape",
            }
            first_data = page.evaluate(
                """async (req) => {
                    try {
                        const r = await window.lib.mtop.request(req);
                        // mtop SDK resolves the promise with the full envelope
                        // (api, v, data, ret, retType, ...). retType === 0
                        // means SUCCESS — even successful responses carry
                        // ret:["SUCCESS::调用成功"], so we must NOT use ret.length
                        // as a failure signal. Only retType === -1 or a
                        // non-empty errorCode indicates failure.
                        if (r && typeof r === 'object' && (r.retType === -1 || r.errorCode)) {
                            return { ok: false, error: JSON.stringify({retType: r.retType, errorCode: r.errorCode, ret: r.ret, message: r.message}) };
                        }
                        return { ok: true, data: r };
                    } catch (e) {
                        return { ok: false, error: (e && e.message) ? String(e.message) : JSON.stringify(e) };
                    }
                }""",
                payload,
            )
        except Exception as exc:
            err = f"{api}: probe threw {exc!r}"
            last_errors.append(err)
            logger.warning("fallback: mtop.request probe for %s threw: %r", api, exc)
            continue

        if not isinstance(first_data, dict) or not first_data.get("ok"):
            err = f"{api}: {(first_data or {}).get('error') if isinstance(first_data, dict) else 'no response'}"
            last_errors.append(err)
            logger.warning("fallback: %s probe failed: %s", api, err.split(': ', 1)[1] if ': ' in err else err)
            continue

        # Probe succeeded — pull out the actual data payload. The SDK
        # returns the full mtop envelope (retType, ret, data, ...). The
        # `data` field is a **stringified JSON envelope itself** for the
        # review API: parsing it once gives us {api, v, data, ret, ...}
        # where the *inner* `data` is the real review content. We walk
        # down into the inner `data` to get the actual review records.
        raw_response = first_data.get("data")
        if isinstance(raw_response, str):
            try:
                envelope = json.loads(raw_response)
            except Exception:
                envelope = {}
        elif isinstance(raw_response, dict):
            envelope = raw_response
        else:
            envelope = {}

        # Unwrap nested mtop envelopes (mtop may nest data one or two
        # levels deep depending on whether the response was double-encoded).
        probe_data = envelope
        for _ in range(3):
            if not isinstance(probe_data, dict):
                probe_data = {}
                break
            inner = probe_data.get("data")
            if isinstance(inner, str):
                try:
                    probe_data = json.loads(inner)
                except Exception:
                    break
            elif isinstance(inner, dict):
                probe_data = inner
            else:
                break

        if not _looks_like_review_payload(probe_data):
            err = (
                f"{api}: empty review list in probe "
                f"(unwrapped shape keys={list(probe_data.keys()) if isinstance(probe_data, dict) else type(probe_data).__name__})"
            )
            last_errors.append(err)
            logger.warning(
                "fallback: %s probe returned no review list (keys=%s), trying next candidate",
                api,
                list(probe_data.keys()) if isinstance(probe_data, dict) else type(probe_data).__name__,
            )
            continue

        # We have a working API. Iterate pages.
        added += 1
        if collector._ingest_payload(url_template, probe_data):
            page_count += 1
        else:
            logger.warning("fallback: %s ingest_payload rejected probe response", api)

        pages_fetched = 1
        hard_limit = max_pages if max_pages and max_pages > 0 else 200
        while True:
            if pages_fetched >= hard_limit:
                break
            if max_pages and pages_fetched >= max_pages:
                break
            next_page_num = pages_fetched + 1
            if collector.latest_total_pages and next_page_num > collector.latest_total_pages:
                break
            try:
                payload["data"] = {**data_tmpl(item_id, next_page_num), **extra_params}
                result = page.evaluate(
                    """async (req) => {
                        try {
                            const r = await window.lib.mtop.request(req);
                            if (r && typeof r === 'object' && (r.retType === -1 || r.errorCode)) {
                                return { ok: false, error: JSON.stringify({retType: r.retType, errorCode: r.errorCode, ret: r.ret, message: r.message}) };
                            }
                            return { ok: true, data: r };
                        } catch (e) {
                            return { ok: false, error: (e && e.message) ? String(e.message) : JSON.stringify(e) };
                        }
                    }""",
                    payload,
                )
            except Exception as exc:
                logger.warning(
                    "fallback: %s page %d threw %r, stopping pagination (captured=%d pages so far)",
                    api, next_page_num, exc, page_count,
                )
                break

            if not isinstance(result, dict) or not result.get("ok"):
                err = (result or {}).get("error") if isinstance(result, dict) else "no response"
                logger.warning(
                    "fallback: %s page %d failed (%s), stopping pagination (captured=%d pages so far)",
                    api, next_page_num, err, page_count,
                )
                break

            page_data_raw = result.get("data")
            if isinstance(page_data_raw, str):
                try:
                    envelope = json.loads(page_data_raw)
                except Exception:
                    envelope = {}
            elif isinstance(page_data_raw, dict):
                envelope = page_data_raw
            else:
                envelope = {}

            page_data = envelope
            for _ in range(3):
                if not isinstance(page_data, dict):
                    page_data = {}
                    break
                inner = page_data.get("data")
                if isinstance(inner, str):
                    try:
                        page_data = json.loads(inner)
                    except Exception:
                        break
                elif isinstance(inner, dict):
                    page_data = inner
                else:
                    break

            if not _looks_like_review_payload(page_data):
                break

            accepted = collector._ingest_payload(url_template, page_data)
            if not accepted:
                logger.info("fallback: %s duplicate/rejected page %d, stopping", api, next_page_num)
                break
            page_count += 1
            pages_fetched += 1

        # ---- Fallback-path gap fill: align captured pages to total_pages ----
        total_pages_fb = collector.latest_total_pages
        if total_pages_fb and (
            not max_pages or collector.batch_count < max_pages
        ):
            gap_added = _fill_gap_via_api(
                page, item_id, sort, collector, candidate,
                total_pages_fb, max_pages,
            )
            page_count += gap_added
            if gap_added:
                logger.info(
                    "fill-gap-api: %d additional page(s) fetched via %s "
                    "(now %d / %d)",
                    gap_added, api,
                    len(collector.captured_pages), total_pages_fb,
                )

        logger.info(
            "fallback: %s fetched %d page(s) directly via window.lib.mtop (captured=%d pages total)",
            api, page_count, len(collector.captured_pages),
        )
        # First working candidate wins.
        return page_count

    raise RuntimeError(
        "Direct mtop API call failed for every candidate. "
        f"Errors: {last_errors}"
    )


def _looks_like_review_payload(payload: Any) -> bool:
    """Cheap heuristic: does the payload contain anything that smells like
    a review list? Used by the direct-API fallback to decide whether to
    iterate further pages.
    """
    if not isinstance(payload, dict):
        return False
    return bool(locate_review_list(payload))


def scrape_reviews(
    item_url: str,
    browser: str = "chrome",
    profile_dir: str | Path = ".tmall-profile",
    headless: bool = True,
    sort: str = "default",
    login_timeout: int = 300,
    max_pages: int = 0,
    manual_wait: int = 0,
    scan_all_responses: bool = False,
    task_id: str | None = None,
    on_batch: Callable[[ReviewBatch, "ReviewCollector"], None] | None = None,
) -> tuple[list[dict[str, Any]], list[ReviewBatch]]:
    """Scrape reviews from a Tmall product page.

    Returns (rows, batches). When ``task_id`` is supplied, every captured
    batch and review is persisted to the database tagged with that task.

    ``on_batch`` is an optional callback invoked synchronously after each
    batch is added to the collector. It receives the batch and the
    collector so the caller can reach ``captured_pages`` / ``batch_count``
    for progress reporting. Exceptions raised by the callback are logged
    and swallowed so they cannot abort the scrape.
    """
    item_id = _extract_item_id(item_url)
    profile_path = Path(profile_dir).resolve()
    collector = ReviewCollector(scan_all_responses=scan_all_responses, task_id=task_id)
    if on_batch is not None:
        collector.on_batch_captured = lambda batch: on_batch(batch, collector)

    context: BrowserContext | None = None
    page: Page | None = None
    max_relogin_attempts = 1
    relogin_attempts = 0
    try:
        while True:
            # (Re)create the context. The first iteration honours the caller's
            # headless setting; after a relogin we keep the same setting so a
            # headless run stays headless end-to-end once cookies are valid.
            context = create_context(browser, profile_path, headless=headless)
            page = first_page(context)
            page.on("response", collector.maybe_add)

            if ensure_item_page(page, item_url, item_id, login_timeout):
                break

            # Login detected. The current context's cookies are not accepted
            # by Tmall — tear it down (the headed relogin needs the same
            # --user-data-dir lock) and run a visible-browser login flow.
            if relogin_attempts >= max_relogin_attempts:
                raise TimeoutError(
                    "Login still required after re-authentication. "
                    f"Current URL: {page.url}"
                )
            relogin_attempts += 1
            close_context(context)
            context = None
            page = None
            relogin_via_headed_browser(browser, profile_path, login_timeout)
            # Loop back and re-create the context in the caller's preferred mode.

        open_review_section(page, sort)

        if manual_wait > 0:
            print(
                f"Manual mode: waiting {manual_wait}s for manual actions "
                "(please click the review tab/page in browser)."
            )
            page.wait_for_timeout(manual_wait * 1000)

        if not wait_for_new_batch(page, collector, previous_count=0, timeout_ms=15_000):
            for _ in range(4):
                try:
                    page.mouse.wheel(0, 2500)
                except Exception as exc:
                    logger.warning("mouse.wheel during scroll-rescue failed: %s", exc)
                page.wait_for_timeout(2_000)
                if wait_for_new_batch(page, collector, previous_count=0, timeout_ms=3_000):
                    break

        if not collector.batch_count:
            # Fallback: call the review API directly from the browser context.
            # The 2025 layout (pc-detail-ssr-2025) lazy-loads the review tab
            # only after a specific user interaction; in headless mode the
            # tab often never opens, so the mtop.* call is never fired.
            # Bypass the tab UI by calling the API from the page itself —
            # the browser context carries the cookies needed for the request.
            logger.warning(
                "scrape_reviews: no review payload was captured from page events after %d s; "
                "falling back to direct mtop API call from browser context (url=%s)",
                (15 + 4 * 5),
                item_url,
            )
            try:
                _fetch_reviews_via_page(page, item_id, sort, collector, max_pages=max_pages)
            except Exception as exc:
                raise RuntimeError(
                    "No review payload was captured. Please keep browser visible, "
                    "open the review tab manually once, then run again. "
                    f"Current URL: {page.url} (fallback error: {exc})"
                )

        if not collector.batch_count:
            raise RuntimeError(
                "No review payload was captured. Please keep browser visible, "
                "open the review tab manually once, then run again. "
                f"Current URL: {page.url}"
            )

        # ---- Phase 1: basic pagination (preserves original semantics) ----
        pages_scraped = 1
        while True:
            if max_pages and pages_scraped >= max_pages:
                break
            previous_count = collector.batch_count
            if not click_next_page(page):
                break
            if not wait_for_new_batch(page, collector, previous_count=previous_count, timeout_ms=10_000):
                break
            pages_scraped += 1

        # ---- Phase 2: fill any missing pages using the latest known total_pages ----
        total_pages = collector.latest_total_pages
        if total_pages:
            _maybe_fill_gap(page, collector, total_pages, max_pages)

            # ---- Phase 3: retry a few more rounds in case new pages are still missing ----
            gap_retries = 0
            while (
                len(collector.captured_pages) < total_pages
                and gap_retries < 3
                and (not max_pages or collector.batch_count < max_pages)
            ):
                before = collector.batch_count
                _maybe_fill_gap(page, collector, total_pages, max_pages, max_retries_per_missing=1)
                if collector.batch_count == before:
                    gap_retries += 1
                else:
                    gap_retries = 0
            if len(collector.captured_pages) < total_pages:
                missing = total_pages - len(collector.captured_pages)
                logger.warning(
                    "Scrape ended with %d missing page(s) out of %d (captured=%d). "
                    "Tmall may have rate-limited or returned empty payloads.",
                    missing, total_pages, len(collector.captured_pages),
                )

        collector.persist_to_database(item_id, source_url=item_url)
        print(f"Captured {len(collector.rows)} unique reviews from {collector.batch_count} review payload(s).")
        return collector.rows, collector.batches
    finally:
        if context is not None:
            close_context(context)
