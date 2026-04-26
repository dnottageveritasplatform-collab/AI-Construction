/**
 * vr_training.js
 * --------------
 * Client-side logic for the VR Training Hub page.
 *
 * Features:
 *  - Fetch modules from /api/dashboard/vr-training (same project/user scope as Dashboard)
 *  - Progress bars with animated fill
 *  - Status filter tabs (All, Passed, In Progress, Pending) — client-side filter
 *  - "Launch" module button → POST /api/vr/modules/:id/launch
 *  - Overall completion KPI (mandatory counts match Dashboard when filter is All)
 */

"use strict";

let activeStatusFilter = "all";

/* ================================================================== */
/*  Project context — shared localStorage with Dashboard / other pages  */
/* ================================================================== */

const VR_USER_ID = "usr-001";

const _vrSP = new URLSearchParams(window.location.search);
let vrActiveProjectId =
    (window.VeritasProjectContext?.parseFromUrl(_vrSP)) ||
    (window.VeritasProjectContext?.readPersisted()) ||
    "";

function vrApiUrl(path) {
    const sep = path.includes("?") ? "&" : "?";
    return vrActiveProjectId
        ? `${path}${sep}project_id=${encodeURIComponent(vrActiveProjectId)}`
        : path;
}

/** Dashboard VR endpoint — requires user_id; optional project_id (server default if omitted). */
function vrDashboardTrainingUrl() {
    const params = new URLSearchParams({ user_id: VR_USER_ID });
    if (vrActiveProjectId) params.set("project_id", vrActiveProjectId);
    return `/api/dashboard/vr-training?${params}`;
}

async function loadVrProjectSwitcher() {
    if (!window.VeritasProjectContext) return;
    const wrap = document.getElementById("projectSwitcher");
    if (!wrap) return;
    try {
        const projects = await VeritasProjectContext.fetchProjectsList(VR_USER_ID);
        if (!projects.length) return;

        vrActiveProjectId = VeritasProjectContext.resolveActiveId(projects, vrActiveProjectId);
        if (!vrActiveProjectId) return;

        window.VeritasProjectContext.writePersisted(vrActiveProjectId);
        const url = new URL(window.location.href);
        url.searchParams.set("project_id", vrActiveProjectId);
        window.history.replaceState({}, "", url);

        wrap.innerHTML = `
            <select id="projectSelect" onchange="switchVrProject(this.value)"
                style="background:var(--bg-card,#1C1C1E);color:var(--text-main,#fff);
                       border:1px solid var(--border,#333);padding:6px 12px;
                       border-radius:8px;font-size:0.85rem;cursor:pointer;min-width:220px;">
                ${projects.map(p => `
                    <option value="${p.id}" ${p.id === vrActiveProjectId ? "selected" : ""}>
                        ${p.name} · ${p.completion}%
                    </option>
                `).join("")}
            </select>
        `;
    } catch (e) {
        console.warn("[VR Switcher] Could not load projects:", e);
    }
}

async function switchVrProject(projectId) {
    vrActiveProjectId = projectId;
    window.VeritasProjectContext?.writePersisted(projectId);
    const sel = document.getElementById("projectSelect");
    if (sel && sel.value !== projectId) sel.value = projectId;
    const url = new URL(window.location.href);
    url.searchParams.set("project_id", projectId);
    window.history.replaceState({}, "", url);
    updateVrNavLinks();
    await loadModules();
    if (typeof showToast === "function") showToast("Switched project context.", "info");
}

function updateVrNavLinks() {
    const pid = encodeURIComponent(vrActiveProjectId);
    const home = document.getElementById("navLinkHome");
    const recent = document.getElementById("navLinkRecentAlerts");
    const rp = document.getElementById("navLinkResourcePlan");
    const vr = document.getElementById("navLinkVrTraining");
    const rl = document.getElementById("navLinkResourciist");
    if (home) {
        home.href = vrActiveProjectId ? `/dashboard?project=${pid}` : "/dashboard";
    }
    if (recent) {
        recent.href = vrActiveProjectId ? `/safety?project_id=${pid}` : "/safety";
    }
    if (rp) {
        rp.href = vrActiveProjectId ? `/resource-plan?project_id=${pid}` : "/resource-plan";
    }
    if (vr) {
        vr.href = vrActiveProjectId ? `/vr-training?project_id=${pid}` : "/vr-training";
    }
    if (rl) {
        rl.href = vrActiveProjectId ? `/resourciist?project_id=${pid}` : "/resourciist";
    }
}

/* ================================================================== */
/*  Module card renderer                                                */
/* ================================================================== */

const STATUS_LABELS = {
    passed:      { label: "Passed",      colour: "var(--accent-green)"  },
    in_progress: { label: "Resuming…",   colour: "var(--accent-blue)"   },
    pending:     { label: "Pending",      colour: "var(--text-secondary)"},
};

const FILL_CLASS = {
    passed:      "fill-green",
    in_progress: "fill-blue",
    pending:     "fill-grey",
};

function renderModuleCard(mod) {
    const st     = STATUS_LABELS[mod.status] ?? STATUS_LABELS.pending;
    const fillCls= FILL_CLASS[mod.status]   ?? "fill-grey";
    const dueTxt = mod.due_date ? `· Due ${formatDate(mod.due_date)}` : "";
    const durMin = mod.duration_min;
    const durStr = durMin >= 60
        ? `${Math.floor(durMin / 60)}h ${durMin % 60 > 0 ? durMin % 60 + "m" : ""}`.trim()
        : `${durMin} mins`;

    return `
    <div class="course-card">
        <div class="course-thumbnail">
            <span class="course-badge">${mod.category || mod.assignment || "VR"}</span>
            <svg class="course-icon" viewBox="0 0 24 24">
                <path d="M20,12V10H4V20H2V4H4V8H20V6H22V12H20M13,18H15V14H13V18M17,18H19V14H17V18M9,18H11V14H9V18Z" fill="currentColor"/>
            </svg>
        </div>
        <div class="course-content">
            <div>
                <div class="course-title">${mod.title}</div>
                <div class="course-meta">
                    <span style="color:${st.colour}">${st.label}</span>
                    · ${durStr} ${dueTxt}
                </div>
            </div>
            <div class="progress-container">
                <div class="progress-header">
                    <span style="color:${st.colour}">${st.label}</span>
                    <span>${mod.completion}%</span>
                </div>
                <div class="progress-track">
                    <div class="progress-fill ${fillCls}" style="width:${mod.completion}%"></div>
                </div>
            </div>
            ${mod.status !== "passed"
                ? `<button class="btn btn-primary btn-sm" style="margin-top:1rem;width:100%"
                           data-module-id="${mod.id}">
                       ${mod.status === "in_progress" ? "▶ Resume Module" : "▶ Start Module"}
                   </button>`
                : `<div style="margin-top:1rem;text-align:center;color:var(--accent-green);font-size:0.85rem;font-weight:600">✓ Completed</div>`
            }
        </div>
    </div>`;
}

/* ================================================================== */
/*  Load modules                                                        */
/* ================================================================== */

async function loadModules() {
    const grid = document.getElementById("vrGrid");
    if (!grid) return;

    showSkeletons("vrGrid", 3, 260);

    try {
        const json = await apiFetch(vrDashboardTrainingUrl());
        const allModules = json.data || [];
        const filtered = activeStatusFilter === "all"
            ? allModules
            : allModules.filter(m => m.status === activeStatusFilter);

        const overallBadge = document.getElementById("vrOverallBadge");
        if (overallBadge) {
            if (activeStatusFilter === "all" && json.mandatory_total > 0) {
                overallBadge.textContent = `${json.mandatory_done}/${json.mandatory_total} Mandatory`;
            } else if (activeStatusFilter === "all") {
                overallBadge.textContent = `${json.overall_pct}% Overall`;
            } else {
                const avg = filtered.length
                    ? Math.round(filtered.reduce((s, m) => s + (Number(m.completion) || 0), 0) / filtered.length)
                    : 0;
                overallBadge.textContent = `${avg}% Avg`;
            }
        }

        const completedBadge = document.getElementById("vrCompletedBadge");
        if (completedBadge) {
            if (activeStatusFilter === "all") {
                const total = json.total_modules ?? allModules.length;
                completedBadge.textContent = `${json.completed_count ?? 0}/${total} Completed`;
            } else {
                const done = filtered.filter(m => m.status === "passed").length;
                completedBadge.textContent = `${done}/${filtered.length} Completed`;
            }
        }

        if (!filtered.length) {
            grid.innerHTML = allModules.length
                ? `<p class="text-muted" style="padding:1rem 0;color:var(--text-muted)">No modules match the selected filter.</p>`
                : `<p class="text-muted" style="padding:1rem 0;color:var(--text-muted)">No VR modules assigned for this project.</p>`;
            return;
        }

        grid.innerHTML = filtered.map(renderModuleCard).join("");

        grid.querySelectorAll("[data-module-id]").forEach(btn => {
            btn.addEventListener("click", () => launchModule(btn.dataset.moduleId, btn));
        });
    } catch (err) {
        console.error("[VR] Modules error:", err);
        const gridEl = document.getElementById("vrGrid");
        if (gridEl) {
            gridEl.innerHTML = `<p class="text-muted" style="padding:1rem 0;color:var(--accent-red)">Could not load VR modules.</p>`;
        }
    }
}

/* ================================================================== */
/*  Launch module                                                       */
/* ================================================================== */

async function launchModule(moduleId, btn) {
    const original = btn.textContent;
    btn.textContent = "Launching…";
    btn.disabled    = true;

    try {
        const json = await apiFetch(vrApiUrl(`/api/vr/modules/${moduleId}/launch`), { method: "POST" });
        showToast(`VR session started: ${json.session.module_id}`, "success");
    } catch (err) {
        showToast("Could not launch VR module. Please try again.", "error");
        btn.textContent = original;
        btn.disabled    = false;
    }
}

/* ================================================================== */
/*  Filter tabs                                                         */
/* ================================================================== */

initFilterTabs("#vrFilterTabs", value => {
    const map = { "All": "all", "Passed": "passed", "In Progress": "in_progress", "Pending": "pending" };
    activeStatusFilter = map[value] ?? "all";
    loadModules();
});

/* ================================================================== */
/*  Init                                                                */
/* ================================================================== */
document.addEventListener("DOMContentLoaded", async () => {
    await loadVrProjectSwitcher();
    updateVrNavLinks();
    await loadModules();
});
