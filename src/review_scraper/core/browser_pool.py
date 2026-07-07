"""Browser instance pool for reusing Playwright contexts across scraping tasks."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from playwright.sync_api import BrowserContext, sync_playwright

logger = logging.getLogger(__name__)


class BrowserPool:
    """Manages long-lived browser contexts for reuse.

    Instead of launching a new browser per scraping task, this pool
    keeps a bounded set of contexts alive and reuses them.
    """

    def __init__(self, max_contexts: int = 3, idle_ttl_seconds: float = 300.0) -> None:
        self._max_contexts = max_contexts
        self._idle_ttl = idle_ttl_seconds
        self._lock = threading.Lock()
        self._playwright: Any = None
        self._contexts: list[tuple[BrowserContext, float, str | None, str]] = []  # (context, last_used, proxy, id)
        self._cleanup_thread: threading.Thread | None = None
        self._stop_cleanup = threading.Event()

    def acquire(self, proxy: str | None = None, profile_dir: str | None = None) -> BrowserContext:
        with self._lock:
            if self._playwright is None:
                self._playwright = sync_playwright().start()
                self._start_cleanup_thread()

            now = time.monotonic()

            # Try to find a reusable context
            for i, (ctx, last_used, ctx_proxy, ctx_id) in enumerate(self._contexts):
                if ctx_proxy == proxy and not self._is_context_closed(ctx):
                    self._contexts[i] = (ctx, now, ctx_proxy, ctx_id)
                    logger.info(f"Reusing browser context {ctx_id}")
                    return ctx

            # Evict idle contexts over TTL
            self._evict_idle_contexts()

            # If at capacity, close the oldest idle
            if len(self._contexts) >= self._max_contexts:
                oldest = min(self._contexts, key=lambda item: item[1])
                oldest_ctx, _, _, oldest_id = oldest
                self._contexts.remove(oldest)
                try:
                    oldest_ctx.close()
                    logger.info(f"Closed oldest browser context {oldest_id}")
                except Exception as e:
                    logger.warning(f"Error closing context {oldest_id}: {e}")

            # Create new context
            context_id = f"ctx-{int(now)}"
            logger.info(f"Creating new browser context {context_id}")

            if profile_dir:
                context = self._playwright.chromium.launch_persistent_context(
                    profile_dir,
                    headless=True,
                    viewport={"width": 1440, "height": 1200},
                )
            else:
                browser = self._playwright.chromium.launch(headless=True)
                context = browser.new_context(
                    viewport={"width": 1440, "height": 1200},
                )

            self._contexts.append((context, now, proxy, context_id))

            # Update metrics
            from review_scraper.core.metrics import browser_contexts_active, browser_contexts_created_total
            browser_contexts_created_total.inc()
            browser_contexts_active.set(len(self._contexts))

            return context

    def release(self, context: BrowserContext) -> None:
        with self._lock:
            for i, (ctx, _, proxy, cid) in enumerate(self._contexts):
                if ctx is context:
                    self._contexts[i] = (ctx, time.monotonic(), proxy, cid)
                    logger.debug(f"Released browser context {cid}")
                    return

    def _evict_idle_contexts(self) -> None:
        """Remove contexts that have exceeded idle TTL."""
        now = time.monotonic()
        to_remove = []

        for ctx, last_used, proxy, cid in self._contexts:
            if now - last_used > self._idle_ttl or self._is_context_closed(ctx):
                to_remove.append((ctx, proxy, cid))

        for ctx, proxy, cid in to_remove:
            self._contexts = [(c, lu, p, id_) for c, lu, p, id_ in self._contexts if id_ != cid]
            try:
                ctx.close()
                logger.info(f"Evicted idle browser context {cid}")
                from review_scraper.core.metrics import browser_contexts_evicted_total, browser_contexts_active
                browser_contexts_evicted_total.inc()
                browser_contexts_active.set(len(self._contexts))
            except Exception as e:
                logger.warning(f"Error closing context {cid}: {e}")

    def _is_context_closed(self, context: BrowserContext) -> bool:
        """Check if a context is closed or invalid."""
        try:
            # Try to access a property to verify the context is still valid
            _ = context.pages
            return False
        except Exception:
            return True

    def _start_cleanup_thread(self) -> None:
        """Start background thread for periodic cleanup."""
        if self._cleanup_thread is None or not self._cleanup_thread.is_alive():
            self._stop_cleanup.clear()
            self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
            self._cleanup_thread.start()
            logger.info("Started browser pool cleanup thread")

    def _cleanup_loop(self) -> None:
        """Background loop to periodically evict idle contexts."""
        while not self._stop_cleanup.is_set():
            time.sleep(60)  # Check every minute
            with self._lock:
                self._evict_idle_contexts()

    def close_all(self) -> None:
        with self._lock:
            self._stop_cleanup.set()
            for ctx, _, _, cid in self._contexts:
                try:
                    ctx.close()
                    logger.info(f"Closed browser context {cid}")
                except Exception as e:
                    logger.warning(f"Error closing context {cid}: {e}")
            self._contexts.clear()
            if self._playwright:
                try:
                    self._playwright.stop()
                    logger.info("Stopped Playwright")
                except Exception as e:
                    logger.warning(f"Error stopping Playwright: {e}")
                self._playwright = None

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._contexts)


_pool: BrowserPool | None = None


def get_browser_pool() -> BrowserPool:
    global _pool
    if _pool is None:
        _pool = BrowserPool()
    return _pool
