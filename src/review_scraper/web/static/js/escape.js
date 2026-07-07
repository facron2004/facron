// Shared HTML escape helpers used by Jinja templates to render untrusted
// API strings safely via innerHTML. Templates must include this script
// before they reference window.escapeHtml / window.escapeAttr.
//
// All Tmall review data (user_nick, sku, content, error messages, etc.)
// is external input — do NOT interpolate it raw into innerHTML.

(function () {
  'use strict';

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  // Escape but preserve " / ' so attribute values are safe to inline.
  function escapeAttr(value) {
    return escapeHtml(value);
  }

  // Safe numeric formatter with fallback.
  function safeNumber(value, fallback) {
    var n = Number(value);
    if (!isFinite(n)) {
      return fallback == null ? 0 : fallback;
    }
    return n;
  }

  window.escapeHtml = escapeHtml;
  window.escapeAttr = escapeAttr;
  window.safeNumber = safeNumber;
})();