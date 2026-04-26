/**
 * utils.js
 * --------
 * Shared utility functions used across all pages of the
 * Veritas AI Construction Platform.
 *
 * Load BEFORE any page-specific script:
 *   <script src="/static/js/utils.js"></script>
 *   <script src="/static/js/dashboard.js"></script>
 */

"use strict";

/* ================================================================== */
/*  DOM helpers                                                         */
/* ================================================================== */

/**
 * Select a single element by CSS selector.
 * @param {string} selector
 * @param {Element} [ctx=document]
 * @returns {Element|null}
 */
function qs(selector, ctx = document) {
    return ctx.querySelector(selector);
}

/**
 * Select all elements matching a CSS selector.
 * @param {string} selector
 * @param {Element} [ctx=document]
 * @returns {NodeList}
 */
function qsa(selector, ctx = document) {
    return ctx.querySelectorAll(selector);
}

/**
 * getElementById shorthand.
 * @param {string} id
 */
function $id(id) {
    return document.getElementById(id);
}

/* ================================================================== */
/*  Toast notifications                                                  */
/* ================================================================== */

let _toastTimer;

/**
 * Display a temporary toast message at the bottom-right of the screen.
 * Requires a <div id="toast"></div> in the page and the CSS from main.css.
 *
 * @param {string} message
 * @param {'success'|'error'|'info'} [type='info']
 * @param {number} [durationMs=3500]
 */
function showToast(message, type = "info", durationMs = 3500) {
    const toast = $id("toast");
    if (!toast) return;

    toast.textContent = message;
    toast.className   = `show ${type}`;

    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => { toast.className = ""; }, durationMs);
}

/* ================================================================== */
/*  API fetch helper                                                    */
/* ================================================================== */

/**
 * Fetch JSON from a platform API endpoint.
 * Throws an Error on non-2xx HTTP responses.
 *
 * @param {string} path         - e.g. "/api/dashboard/alerts"
 * @param {RequestInit} [opts]  - Optional fetch options (method, body, etc.)
 * @returns {Promise<Object>}
 */
async function apiFetch(path, opts = {}) {
    const defaults = {
        headers: { "Content-Type": "application/json" },
    };
    const res = await fetch(path, { ...defaults, ...opts });
    if (!res.ok) {
        throw new Error(`API ${res.status} – ${path}`);
    }
    return res.json();
}

/* ================================================================== */
/*  Skeleton loaders                                                    */
/* ================================================================== */

/**
 * Replace the contents of a container with N skeleton placeholder rows.
 * @param {string|Element} containerOrId
 * @param {number} [count=3]
 * @param {number} [heightPx=48]
 */
function showSkeletons(containerOrId, count = 3, heightPx = 48) {
    const el = typeof containerOrId === "string"
        ? $id(containerOrId)
        : containerOrId;
    if (!el) return;
    el.innerHTML = Array.from({ length: count })
        .map(() => `<div class="skeleton" style="height:${heightPx}px;margin-bottom:8px;"></div>`)
        .join("");
}

/* ================================================================== */
/*  Date / time helpers                                                  */
/* ================================================================== */

/**
 * Return a human-readable "time ago" string.
 * @param {Date|string} date
 * @returns {string}  e.g. "3 minutes ago"
 */
function timeAgo(date) {
    const diff = Math.floor((Date.now() - new Date(date).getTime()) / 1000);
    if (diff <  60)  return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400)return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
}

/**
 * Format a YYYY-MM-DD string as a locale-friendly date.
 * @param {string} dateStr
 * @returns {string}  e.g. "Aug 20, 2026"
 */
function formatDate(dateStr) {
    if (!dateStr) return "—";
    return new Date(dateStr).toLocaleDateString("en-US", {
        year: "numeric", month: "short", day: "numeric",
    });
}

/* ================================================================== */
/*  Navigation active-link highlighter                                  */
/* ================================================================== */

/**
 * Mark the nav link that matches the current page path as active.
 * Call once per page after the DOM is ready.
 */
function highlightActiveNavLink() {
    const path = window.location.pathname.replace(/\/$/, "") || "/dashboard";
    qsa(".nav-link").forEach(link => {
        const href = link.getAttribute("href")?.replace(/\/$/, "") || "";
        link.classList.toggle("active", href === path || (path === "/" && href === "/dashboard"));
    });
}

/* ================================================================== */
/*  Filter tab helper                                                   */
/* ================================================================== */

/**
 * Initialise a set of filter buttons so clicking one deactivates the others.
 *
 * @param {string}   containerSelector  - CSS selector for the button container
 * @param {Function} onSelect           - Callback(value) when a tab is clicked
 */
function initFilterTabs(containerSelector, onSelect) {
    const container = qs(containerSelector);
    if (!container) return;

    container.addEventListener("click", e => {
        const btn = e.target.closest(".filter-btn");
        if (!btn) return;

        qsa(".filter-btn", container).forEach(b => b.classList.remove("active"));
        btn.classList.add("active");

        if (typeof onSelect === "function") onSelect(btn.dataset.value ?? btn.textContent.trim());
    });
}

/* ================================================================== */
/*  Progress bar helper                                                  */
/* ================================================================== */

/**
 * Animate a .progress-fill element to a target width percentage.
 * @param {Element|string} fillElOrId
 * @param {number} pct - 0 to 100
 */
function setProgress(fillElOrId, pct) {
    const el = typeof fillElOrId === "string" ? $id(fillElOrId) : fillElOrId;
    if (!el) return;
    // Defer so CSS transition fires
    requestAnimationFrame(() => { el.style.width = `${Math.min(100, Math.max(0, pct))}%`; });
}

/* ================================================================== */
/*  User Menu Dropdown                                                  */
/* ================================================================== */

/**
 * Initialise the avatar user-menu dropdown.
 * Closes when clicking outside or pressing Escape.
 */
function initUserMenu() {
    const wrap     = $id("userMenuWrap");
    const dropdown = $id("userDropdown");
    const avatar   = $id("avatarBtn");
    if (!wrap || !dropdown || !avatar) return;

    avatar.addEventListener("click", e => {
        e.stopPropagation();
        dropdown.classList.toggle("open");
    });

    document.addEventListener("click", e => {
        if (!wrap.contains(e.target)) {
            dropdown.classList.remove("open");
        }
    });

    document.addEventListener("keydown", e => {
        if (e.key === "Escape") dropdown.classList.remove("open");
    });
}

/* ================================================================== */
/*  Logout                                                              */
/* ================================================================== */

/** Show the logout confirmation modal. */
function showLogoutModal() {
    const dropdown = $id("userDropdown");
    if (dropdown) dropdown.classList.remove("open");

    const overlay = $id("logoutOverlay");
    if (overlay) overlay.classList.add("open");
}

/** Hide the logout confirmation modal. */
function hideLogoutModal() {
    const overlay = $id("logoutOverlay");
    if (overlay) overlay.classList.remove("open");

    // Reset button state in case it was mid-flight
    const btn = $id("confirmLogoutBtn");
    if (btn) { btn.textContent = "Sign Out"; btn.disabled = false; }
}

/**
 * Perform the logout: calls POST /api/auth/logout, then redirects to /login.
 */
async function confirmLogout() {
    const btn = $id("confirmLogoutBtn");
    if (btn) { btn.textContent = "Signing out…"; btn.disabled = true; }

    try {
        await fetch("/api/auth/logout", { method: "POST" });
    } catch (_) {
        // Best-effort — proceed to redirect regardless
    }

    window.location.href = "/login?signedout=1";
}

/* ================================================================== */
/*  Auto-init on DOMContentLoaded                                       */
/* ================================================================== */
document.addEventListener("DOMContentLoaded", () => {
    highlightActiveNavLink();
    initUserMenu();
});