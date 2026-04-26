/**
 * resourciist.js
 * --------------
 * Client-side logic for the Resourciist (Asset Inventory) page.
 *
 * Features:
 *  - Fetch and render asset cards from /api/resources/assets
 *  - Filter by category tab (All, Personnel, Heavy Machinery, Materials)
 *  - Live keyword search (filters rendered cards instantly)
 *  - Status badge colour mapping
 *  - Dashboard summary KPI refresh
 */

"use strict";

/* Active filter state */
let activeCategory = "all";
let searchQuery    = "";

/** Last fetched rows (for Edit without stuffing data-* on cards). */
let rlLoadedAssets = [];

function escapeHtml(s) {
    if (s == null) return "";
    return String(s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

/* ================================================================== */
/*  Project context — shared localStorage with Dashboard / other pages */
/* ================================================================== */

const RESOURCIIST_USER_ID = "usr-001";

const _rlSP = new URLSearchParams(window.location.search);
let rlActiveProjectId =
    (window.VeritasProjectContext?.parseFromUrl(_rlSP)) ||
    (window.VeritasProjectContext?.readPersisted()) ||
    "";

function rlApiUrl(path) {
    const sep = path.includes("?") ? "&" : "?";
    return rlActiveProjectId
        ? `${path}${sep}project_id=${encodeURIComponent(rlActiveProjectId)}`
        : path;
}

async function loadResourciistProjectSwitcher() {
    if (!window.VeritasProjectContext) return;
    const wrap = document.getElementById("projectSwitcher");
    if (!wrap) return;
    try {
        const projects = await VeritasProjectContext.fetchProjectsList(RESOURCIIST_USER_ID);
        if (!projects.length) return;

        rlActiveProjectId = VeritasProjectContext.resolveActiveId(projects, rlActiveProjectId);
        if (!rlActiveProjectId) return;

        window.VeritasProjectContext.writePersisted(rlActiveProjectId);
        const url = new URL(window.location.href);
        url.searchParams.set("project_id", rlActiveProjectId);
        window.history.replaceState({}, "", url);

        wrap.innerHTML = `
            <select id="projectSelect" onchange="switchResourciistProject(this.value)"
                style="background:var(--bg-card,#1C1E1E);color:var(--text-main,#fff);
                       border:1px solid var(--border,#333);padding:6px 12px;
                       border-radius:8px;font-size:0.85rem;cursor:pointer;min-width:220px;">
                ${projects.map(p => `
                    <option value="${p.id}" ${p.id === rlActiveProjectId ? "selected" : ""}>
                        ${p.name} · ${p.completion}%
                    </option>
                `).join("")}
            </select>
        `;
        updateAddResourceUi();
    } catch (e) {
        console.warn("[Resource List Switcher] Could not load projects:", e);
    }
}

async function switchResourciistProject(projectId) {
    rlActiveProjectId = projectId;
    window.VeritasProjectContext?.writePersisted(projectId);
    const sel = document.getElementById("projectSelect");
    if (sel && sel.value !== projectId) sel.value = projectId;
    const url = new URL(window.location.href);
    url.searchParams.set("project_id", projectId);
    window.history.replaceState({}, "", url);
    updateResourciistNavLinks();
    updateAddResourceUi();
    await loadResourceSummary();
    await loadAssets();
    if (typeof showToast === "function") showToast("Switched project context.", "info");
}

function updateAddResourceUi() {
    const btn = document.getElementById("btnAddResource");
    if (!btn) return;
    const ok = Boolean(rlActiveProjectId);
    btn.disabled = !ok;
    btn.title = ok ? "Add asset to this project" : "Select a project in the dropdown first";
}

function updateResourciistNavLinks() {
    const pid = encodeURIComponent(rlActiveProjectId);
    const home = document.getElementById("navLinkHome");
    const recent = document.getElementById("navLinkRecentAlerts");
    const rp = document.getElementById("navLinkResourcePlan");
    const vr = document.getElementById("navLinkVrTraining");
    const rl = document.getElementById("navLinkResourciist");
    if (home) {
        home.href = rlActiveProjectId ? `/dashboard?project=${pid}` : "/dashboard";
    }
    if (recent) {
        recent.href = rlActiveProjectId ? `/safety?project_id=${pid}` : "/safety";
    }
    if (rp) {
        rp.href = rlActiveProjectId ? `/resource-plan?project_id=${pid}` : "/resource-plan";
    }
    if (vr) {
        vr.href = rlActiveProjectId ? `/vr-training?project_id=${pid}` : "/vr-training";
    }
    if (rl) {
        rl.href = rlActiveProjectId ? `/resourciist?project_id=${pid}` : "/resourciist";
    }
}

/* ================================================================== */
/*  Status pill helpers                                                 */
/* ================================================================== */

const STATUS_CONFIG = {
    available: { label: "Available", cls: "status-green" },
    in_use:    { label: "In Use",    cls: "status-blue" },
    low_stock: { label: "Low Stock", cls: "status-orange" },
};

function statusPill(status) {
    const cfg = STATUS_CONFIG[status] ?? { label: status, cls: "" };
    return `<div class="status-pill ${cfg.cls}">${cfg.label}</div>`;
}

/* ================================================================== */
/*  Asset card renderer                                                 */
/* ================================================================== */

const CATEGORY_ICONS = {
    "Personnel":       `<svg width="32" height="32" viewBox="0 0 24 24" fill="#aaa"><path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg>`,
    "Heavy Machinery": `<svg width="32" height="32" viewBox="0 0 24 24" fill="#FDD835"><path d="M2 17h20v2H2v-2zM3.5 14h17l-1.5-6H5L3.5 14zm3-4h11l1 4H5.5l1-4z"/></svg>`,
    "Materials":       `<svg width="32" height="32" viewBox="0 0 24 24" fill="#90A4AE"><rect x="3" y="3" width="18" height="18" rx="2" fill="none" stroke="#90A4AE" stroke-width="2"/><rect x="7" y="7" width="10" height="10" fill="#90A4AE" opacity="0.4"/></svg>`,
};

function renderAssetCard(asset) {
    const icon = CATEGORY_ICONS[asset.category] ?? CATEGORY_ICONS["Materials"];
    const nm = escapeHtml(asset.name);
    const id = escapeHtml(asset.id);
    const cat = escapeHtml(asset.category);
    const loc = escapeHtml(asset.location);
    const notes = asset.notes ? escapeHtml(asset.notes) : "";
    const actions = rlActiveProjectId
        ? `<div class="asset-card-actions">
            <button type="button" class="btn-edit-resource" data-edit-id="${escapeHtml(asset.id)}" aria-label="Edit ${nm}">Edit</button>
            <button type="button" class="btn-delete-resource" data-delete-id="${escapeHtml(asset.id)}" aria-label="Remove ${nm} from project">Delete</button>
           </div>`
        : "";
    return `
    <div class="asset-card" data-id="${id}">
        <div class="asset-thumb">
            ${icon}
        </div>
        <div class="asset-info">
            <span class="asset-title">${nm}</span>
            <span class="asset-id">
                ID: ${id}
                <span class="asset-category">${cat}</span>
            </span>
            ${notes ? `<span class="asset-notes">${notes}</span>` : ""}
        </div>
        <div class="asset-location text-muted" style="font-size:0.78rem;white-space:nowrap;">${loc}</div>
        ${actions}
        ${statusPill(asset.status)}
    </div>`;
}

/* ================================================================== */
/*  Load & render assets                                                */
/* ================================================================== */

async function loadAssets() {
    const list = document.getElementById("assetList");
    if (!list) return;

    showSkeletons("assetList", 4, 80);

    try {
        const params = new URLSearchParams();
        if (activeCategory !== "all") params.set("category", activeCategory);
        if (searchQuery)              params.set("q",        searchQuery);

        const qs = params.toString();
        const path = qs ? `/api/resources/assets?${qs}` : "/api/resources/assets";
        const json = await apiFetch(rlApiUrl(path));

        if (!json.data.length) {
            rlLoadedAssets = [];
            list.innerHTML = `<p class="text-muted" style="padding:1rem 0">No assets match your filters.</p>`;
            return;
        }

        rlLoadedAssets = json.data;
        list.innerHTML = json.data.map(renderAssetCard).join("");

    } catch (err) {
        console.error("[Resourciist] Load error:", err);
        list.innerHTML = `<p class="text-muted" style="padding:1rem 0">Failed to load assets. Please refresh.</p>`;
    }
}

/* ================================================================== */
/*  Summary KPIs                                                        */
/* ================================================================== */

async function loadResourceSummary() {
    try {
        const json = await apiFetch(rlApiUrl("/api/resources/summary"));
        const d    = json.data;

        const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
        set("kpiInUse",    `${d.in_use} Units`);
        set("kpiStaff",    `${d.staff_on_site} Active`);
        set("kpiLowStock", `${d.low_stock} Items`);

    } catch (err) {
        console.error("[Resourciist] Summary error:", err);
    }
}

/* ================================================================== */
/*  Filter tabs                                                         */
/* ================================================================== */

initFilterTabs("#filterTabs", value => {
    activeCategory = value.toLowerCase() === "all" ? "all" : value;
    loadAssets();
});

/* ================================================================== */
/*  Search                                                              */
/* ================================================================== */

const searchInput = document.getElementById("searchInput");
if (searchInput) {
    let debounceTimer;
    searchInput.addEventListener("input", e => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
            searchQuery = e.target.value.trim();
            loadAssets();
        }, 300);  // debounce 300ms
    });
}

/* ================================================================== */
/*  Add resource modal                                                  */
/* ================================================================== */

function openAddResourceModal() {
    if (!rlActiveProjectId) {
        if (typeof showToast === "function") showToast("Select a project first.", "error");
        return;
    }
    const ov = document.getElementById("addResourceOverlay");
    const form = document.getElementById("addResourceForm");
    if (!ov || !form) return;
    form.reset();
    const hid = document.getElementById("arAssetId");
    if (hid) hid.value = "";
    const title = document.getElementById("addResourceTitle");
    if (title) title.textContent = "Add resource";
    const sub = document.getElementById("addResourceSubmit");
    if (sub) sub.textContent = "Save";
    ov.classList.add("open");
    ov.setAttribute("aria-hidden", "false");
    document.getElementById("arName")?.focus();
}

function openEditResourceModal(asset) {
    if (!rlActiveProjectId) {
        if (typeof showToast === "function") showToast("Select a project first.", "error");
        return;
    }
    if (!asset || !asset.id) return;
    const ov = document.getElementById("addResourceOverlay");
    const form = document.getElementById("addResourceForm");
    if (!ov || !form) return;
    const hid = document.getElementById("arAssetId");
    if (hid) hid.value = asset.id;
    const title = document.getElementById("addResourceTitle");
    if (title) title.textContent = "Edit resource";
    const sub = document.getElementById("addResourceSubmit");
    if (sub) sub.textContent = "Update";
    document.getElementById("arName").value = asset.name || "";
    document.getElementById("arCategory").value = asset.category || "Materials";
    document.getElementById("arStatus").value = asset.status || "available";
    document.getElementById("arLocation").value = asset.location || "";
    document.getElementById("arNotes").value = asset.notes || "";
    ov.classList.add("open");
    ov.setAttribute("aria-hidden", "false");
    document.getElementById("arName")?.focus();
}

function closeAddResourceModal() {
    const ov = document.getElementById("addResourceOverlay");
    if (!ov) return;
    const hid = document.getElementById("arAssetId");
    if (hid) hid.value = "";
    ov.classList.remove("open");
    ov.setAttribute("aria-hidden", "true");
}

async function submitAddResource(ev) {
    ev.preventDefault();
    if (!rlActiveProjectId) {
        if (typeof showToast === "function") showToast("Select a project first.", "error");
        return;
    }
    const submitBtn = document.getElementById("addResourceSubmit");
    const editId = (document.getElementById("arAssetId")?.value || "").trim();
    const name = (document.getElementById("arName")?.value || "").trim();
    const category = document.getElementById("arCategory")?.value || "Materials";
    const status = document.getElementById("arStatus")?.value || "available";
    const location = (document.getElementById("arLocation")?.value || "").trim();
    const notes = (document.getElementById("arNotes")?.value || "").trim();
    if (!name || !location) {
        if (typeof showToast === "function") showToast("Name and location are required.", "error");
        return;
    }
    if (submitBtn) submitBtn.disabled = true;
    try {
        const payload = { name, category, status, location, notes };
        if (editId) {
            const path = `/api/resources/assets/${encodeURIComponent(editId)}`;
            await apiFetch(rlApiUrl(path), {
                method: "PUT",
                body: JSON.stringify(payload),
            });
            if (typeof showToast === "function") showToast("Resource updated.", "success");
        } else {
            await apiFetch(rlApiUrl("/api/resources/assets"), {
                method: "POST",
                body: JSON.stringify(payload),
            });
            if (typeof showToast === "function") showToast("Resource added to project.", "success");
        }
        closeAddResourceModal();
        await loadResourceSummary();
        await loadAssets();
    } catch (err) {
        console.error("[Resourciist] Save resource error:", err);
        if (typeof showToast === "function") {
            showToast(editId ? "Could not update resource." : "Could not add resource.", "error");
        }
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

/* ================================================================== */
/*  Init                                                                */
/* ================================================================== */
document.addEventListener("DOMContentLoaded", async () => {
    document.getElementById("btnAddResource")?.addEventListener("click", openAddResourceModal);
    document.getElementById("addResourceCancel")?.addEventListener("click", closeAddResourceModal);
    document.getElementById("addResourceForm")?.addEventListener("submit", submitAddResource);
    document.getElementById("addResourceOverlay")?.addEventListener("click", e => {
        if (e.target?.id === "addResourceOverlay") closeAddResourceModal();
    });

    document.getElementById("assetList")?.addEventListener("click", e => {
        const editBtn = e.target.closest(".btn-edit-resource");
        if (editBtn) {
            if (!rlActiveProjectId) {
                if (typeof showToast === "function") showToast("Select a project first.", "error");
                return;
            }
            const eid = editBtn.getAttribute("data-edit-id");
            const asset = rlLoadedAssets.find(a => a.id === eid);
            if (asset) openEditResourceModal(asset);
            return;
        }
        const delBtn = e.target.closest(".btn-delete-resource");
        if (delBtn) {
            if (!rlActiveProjectId) {
                if (typeof showToast === "function") showToast("Select a project first.", "error");
                return;
            }
            const did = delBtn.getAttribute("data-delete-id");
            const row = rlLoadedAssets.find(a => a.id === did);
            const label = row ? row.name : did;
            if (!confirm("Remove this resource from the project?\n\n" + (label || did))) return;
            (async () => {
                try {
                    const path = `/api/resources/assets/${encodeURIComponent(did)}`;
                    await apiFetch(rlApiUrl(path), { method: "DELETE" });
                    if (typeof showToast === "function") showToast("Resource removed from project.", "success");
                    await loadResourceSummary();
                    await loadAssets();
                } catch (err) {
                    console.error("[Resourciist] Delete resource error:", err);
                    if (typeof showToast === "function") showToast("Could not remove resource.", "error");
                }
            })();
        }
    });

    await loadResourciistProjectSwitcher();
    updateResourciistNavLinks();
    updateAddResourceUi();
    await loadResourceSummary();
    await loadAssets();
});
