/**
 * dashboard.js
 * ------------
 * Client-side logic for the Veritas AI Construction Platform – Dashboard.
 *
 * PROJECT SCOPING
 * ---------------
 * The active project is resolved from the URL (?project=), then localStorage
 * (shared with Safety Monitor and other module pages), then the first listed project.
 * If still absent, the server falls back to DEFAULT_PROJECT_ID.
 *
 * Every API call that returns widget data passes ?project_id= so the
 * server returns data for THAT project only:
 *   - Recent Alerts  → alerts for the active project's IoT sensors
 *   - Upcoming Tasks → tasks from the active project's Resource Plan
 *   - VR Training    → the logged-in user's modules on the active project
 *
 * The project switcher (top of dashboard) lets the user switch context
 * without reloading the page — it re-fetches all widgets with the new ID.
 */

"use strict";

/* ================================================================== */
/*  State                                                              */
/* ================================================================== */

/** Active project ID — URL, then persisted selection, then switcher default */
const _spInit = new URLSearchParams(window.location.search);
let ACTIVE_PROJECT_ID =
    (window.VeritasProjectContext?.parseFromUrl(_spInit)) ||
    (window.VeritasProjectContext?.readPersisted()) ||
    "";

/** Logged-in user ID (hardcoded for demo; would come from session) */
const CURRENT_USER_ID = "usr-001";

/** Tracked acknowledged alert IDs (session-local) */
const ackedAlerts = new Set();

let toastTimer;

/* ================================================================== */
/*  Utilities                                                           */
/* ================================================================== */

const $ = id => document.getElementById(id);

function formatDate(isoDate) {
    if (!isoDate) return "—";
    const d = new Date(isoDate);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleDateString(undefined, { month: "short", day: "2-digit", year: "numeric" });
}

function formatMillions(amount) {
    const n = Number(amount);
    if (!Number.isFinite(n)) return "0.0M";
    return `${(n / 1_000_000).toFixed(1)}M`;
}

function formatAmount(amount) {
    const n = Number(amount);
    if (!Number.isFinite(n)) return "0";
    return n.toFixed(2);
}

function setSummaryStatus(statusRaw) {
    const status = String(statusRaw || "").toLowerCase();
    const badge = $("summaryProjectStatus");
    if (!badge) return;
    badge.textContent = status ? status.charAt(0).toUpperCase() + status.slice(1) : "Unknown";
}

async function loadProjectSummary() {
    try {
        const json = await apiFetch("/api/dashboard/summary");
        const d = json.data || {};

        const nameEl = $("summaryProjectName");
        const startEl = $("summaryProjectStart");
        const endEl = $("summaryProjectEnd");
        const budgetEl = $("summaryProjectBudget");
        const fillEl = $("summaryBudgetFill");
        const editLink = $("summaryEditProjectLink");
        const spentInput = $("summaryBudgetSpentInput");

        if (nameEl) nameEl.textContent = d.project_name || "Project";
        setSummaryStatus(d.status);
        if (startEl) startEl.textContent = formatDate(d.start_date);
        if (endEl) endEl.textContent = formatDate(d.est_completion);
        if (budgetEl) budgetEl.textContent = `${formatMillions(d.budget_spent)} / ${formatMillions(d.budget_total)}`;
        if (fillEl) {
            const pct = Math.max(0, Math.min(100, Number(d.budget_pct) || 0));
            fillEl.style.width = `${pct}%`;
        }
        if (spentInput) spentInput.value = formatAmount(d.budget_spent);
        if (editLink) {
            const pid = encodeURIComponent(ACTIVE_PROJECT_ID || "");
            editLink.href = `/edit-project?project=${pid}`;
        }
    } catch (err) {
        console.error("[Summary] Load error:", err);
    }
}

async function saveBudgetSpent() {
    const input = $("summaryBudgetSpentInput");
    const btn = $("summaryBudgetSaveBtn");
    if (!input) return;
    const raw = input.value;
    const budgetSpent = Number(raw);
    if (!Number.isFinite(budgetSpent) || budgetSpent < 0) {
        showToast("Enter a valid non-negative budget spent value.", "error");
        return;
    }
    if (!ACTIVE_PROJECT_ID) {
        showToast("No active project selected.", "error");
        return;
    }

    try {
        if (btn) btn.disabled = true;
        const res = await fetch("/api/project/budget", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                project_id: ACTIVE_PROJECT_ID,
                budget_spent: budgetSpent,
            }),
        });
        const json = await res.json();
        if (!res.ok || json.status !== "ok") {
            throw new Error(json.message || `HTTP ${res.status}`);
        }
        showToast("Budget spent updated.", "success");
        await loadProjectSummary();
    } catch (err) {
        console.error("[Summary] Budget save error:", err);
        showToast(`Could not update budget spent: ${err.message || "Unknown error"}`, "error");
    } finally {
        if (btn) btn.disabled = false;
    }
}

function showToast(msg, type = "info") {
    const t = $("toast");
    if (!t) return;
    t.textContent = msg;
    t.className   = `show ${type}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.className = ""; }, 3500);
}

/**
 * Generic JSON fetch helper — automatically appends project_id + user_id.
 * @param {string} path  - API path (may already contain query params)
 * @param {boolean} scoped - if false, skip injecting project/user params
 */
async function apiFetch(path, scoped = true) {
    let url = path;
    if (scoped && ACTIVE_PROJECT_ID) {
        const sep = url.includes("?") ? "&" : "?";
        url += `${sep}project_id=${encodeURIComponent(ACTIVE_PROJECT_ID)}&user_id=${encodeURIComponent(CURRENT_USER_ID)}`;
    }
    const res = await fetch(url);
    if (!res.ok) throw new Error(`API error ${res.status}: ${url}`);
    return res.json();
}

/* ================================================================== */
/*  Project Switcher                                                    */
/* ================================================================== */

/**
 * Load all projects for this user and render the switcher dropdown.
 * Highlights the currently active project.
 */
async function loadProjectSwitcher() {
    try {
        const json = await fetch(`/api/dashboard/projects?user_id=${CURRENT_USER_ID}`).then(r => r.json());
        const wrap = $("projectSwitcher");
        if (!wrap) return;

        let projects = json.data || [];

        // Also pull from the new-project API to catch any published projects
        // that the dashboard API hasn't picked up yet
        try {
            const allRes = await fetch(`/api/new-project/projects`).then(r => r.json());
            const allProjects = allRes.data || [];
            allProjects.forEach(p => {
                if (p.status === "active" && !projects.find(existing => existing.id === p.id)) {
                    projects.push({
                        id:         p.id,
                        name:       localStorage.getItem("project_name_" + p.id) || p.name || p.id,
                        completion: p.completion || 0,
                    });
                }
            });
        } catch { /* non-fatal */ }

        if (!projects.length) return;

        if (window.VeritasProjectContext) {
            ACTIVE_PROJECT_ID = VeritasProjectContext.resolveActiveId(projects, ACTIVE_PROJECT_ID);
            if (ACTIVE_PROJECT_ID) {
                VeritasProjectContext.writePersisted(ACTIVE_PROJECT_ID);
                const url = new URL(window.location.href);
                if (url.searchParams.get("project") !== ACTIVE_PROJECT_ID) {
                    url.searchParams.set("project", ACTIVE_PROJECT_ID);
                    window.history.replaceState({}, "", url);
                }
            }
        } else if (!ACTIVE_PROJECT_ID) {
            ACTIVE_PROJECT_ID = projects[0].id;
        }

        // Stamp project_id onto all module nav links now that we have it
        updateModuleLinks();

        wrap.innerHTML = `
            <select id="projectSelect" onchange="switchProject(this.value)"
                style="background:var(--card-bg,#1C1C1E);color:var(--text-primary,#fff);
                       border:1px solid var(--border-color,#2D2D2D);padding:6px 12px;
                       border-radius:8px;font-size:0.85rem;cursor:pointer;min-width:220px;">
                ${projects.map(p => `
                    <option value="${p.id}" ${p.id === ACTIVE_PROJECT_ID ? "selected" : ""}>
                        ${p.name} · ${p.completion}%
                    </option>
                `).join("")}
            </select>
        `;
    } catch (e) {
        console.warn("[Switcher] Could not load projects:", e);
    }
}

/** Switch the active project and reload all widgets + BIM viewer. */
function switchProject(projectId) {
    ACTIVE_PROJECT_ID = projectId;
    window.VeritasProjectContext?.writePersisted(projectId);
    const sel = document.getElementById("projectSelect");
    if (sel && sel.value !== projectId) sel.value = projectId;
    // Update URL without reload so the state is shareable/bookmarkable
    const url = new URL(window.location.href);
    url.searchParams.set("project", projectId);
    window.history.replaceState({}, "", url);
    updateModuleLinks();
    // Reload Three.js IFC for the newly selected project.
    if (window.IFCViewer && typeof window.IFCViewer.loadIFC === "function") {
        window.IFCViewer.loadIFC(null, projectId);
    }
    // Reload all widgets with the new project context
    refreshAllWidgets();
    showToast("Switched project context.", "info");
}

/** Stamp project_id onto every bottom-card module link so navigating to
 *  Safety Monitor, Resource Plan, VR Training, or the documents strip carries the right context. */
function updateModuleLinks() {
    const pid = encodeURIComponent(ACTIVE_PROJECT_ID);
    const linkSafety       = document.getElementById("linkSafety");
    const linkResourcePlan = document.getElementById("linkResourcePlan");
    const linkVrTraining   = document.getElementById("linkVrTraining");
    const linkProjectDocs  = document.getElementById("linkProjectDocs");
    const navRecentAlerts  = document.getElementById("navLinkRecentAlerts");
    const navVrTraining    = document.getElementById("navLinkVrTraining");
    if (linkSafety)       linkSafety.href       = `/safety?project_id=${pid}`;
    if (linkResourcePlan) linkResourcePlan.href = `/resource-plan?project_id=${pid}`;
    if (linkVrTraining)   linkVrTraining.href   = `/vr-training?project_id=${pid}`;
    if (navRecentAlerts) {
        navRecentAlerts.href = ACTIVE_PROJECT_ID
            ? `/safety?project_id=${encodeURIComponent(ACTIVE_PROJECT_ID)}`
            : "/safety";
    }
    if (navVrTraining) {
        navVrTraining.href = ACTIVE_PROJECT_ID
            ? `/vr-training?project_id=${encodeURIComponent(ACTIVE_PROJECT_ID)}`
            : "/vr-training";
    }
    if (linkProjectDocs) {
        linkProjectDocs.href = ACTIVE_PROJECT_ID
            ? `/dashboard?project=${encodeURIComponent(ACTIVE_PROJECT_ID)}#dashboardDocumentsCard`
            : "/dashboard#dashboardDocumentsCard";
    }
}

/** Reload every dashboard widget (used after project switch). */
async function refreshAllWidgets() {
    await Promise.allSettled([
        loadProjectSummary(),
        loadProgressData(),
        loadAlerts(),
        loadTasks(),
        loadVrTraining(),
        loadDocumentsWidget(),
        loadTeamRoster(),
        loadModelData("all"),
    ]);
}

/**
 * Load the BIM IFC after ACTIVE_PROJECT_ID is known.
 * The inline IFC viewer used to auto-load from ?project= only, which misses
 * the first paint when the project comes from localStorage / switcher default
 * (URL is updated later via replaceState, without reloading).
 */
async function syncIfcViewerWithActiveProject() {
    if (!window.IFCViewer) return;

    let ifcProjectId = ACTIVE_PROJECT_ID;
    if (!ifcProjectId) {
        try {
            const res = await fetch(
                `/api/dashboard/summary?user_id=${encodeURIComponent(CURRENT_USER_ID)}`
            );
            if (res.ok) {
                const json = await res.json();
                if (json.data && json.data.project_id) {
                    ifcProjectId = json.data.project_id;
                }
            }
        } catch (e) {
            console.warn("[Dashboard] Could not resolve project for IFC viewer:", e);
        }
    }

    if (ifcProjectId && typeof window.IFCViewer.loadIFC === "function") {
        window.IFCViewer.loadIFC(null, ifcProjectId);
    } else if (typeof window.IFCViewer.showNoModel === "function") {
        window.IFCViewer.showNoModel();
    }
}

/* ================================================================== */
/*  BIM CANVAS RENDERER  (UC-02)                                        */
/*  Isometric 4-storey building drawn entirely in a <canvas> element.   */
/*  Layers are revealed progressively as the selected phase advances.   */
/* ================================================================== */

const BIM = (() => {
    // ── Geometry ────────────────────────────────────────────────────
    const TILE = 54, ISO_H = 27, FLOOR_H = 50, FLOORS = 4, GX = 3, GZ = 3;

    // ── Colour palette ───────────────────────────────────────────────
    const C = {
        sky1:'#0d1117', sky2:'#161b22', ground:'#1c2128',
        grid:'rgba(100,160,220,0.07)',
        foundTop:'#4a525a', foundFront:'#3d444b', foundSide:'#323a41',
        slabTop:'#3d4a56', slabFront:'#2f3b47', slabSide:'#263340',
        colFront:'#4a90e2', colSide:'#3a6fb5', colTop:'#5ba3f5',
        duct:'#f59e0b', pipe:'#3b82f6',
        wallFront:'#d4d9de', wallSide:'#b8bec4',
        winFill:'rgba(90,180,255,0.28)', winBorder:'rgba(140,210,255,0.75)',
        roofTop:'#52595f', roofFront:'#44505a', roofSide:'#3a464f',
    };

    let _cx = 0, _cy = 0;

    function iso(gx, gy, gz) {
        return { x: _cx + (gx - gz) * TILE, y: _cy - gy * FLOOR_H + (gx + gz) * ISO_H };
    }

    function face(ctx, pts, fill, stroke, alpha = 1) {
        ctx.save();
        if (alpha < 1) ctx.globalAlpha = alpha;
        ctx.beginPath();
        pts.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y));
        ctx.closePath();
        ctx.fillStyle = fill; ctx.fill();
        if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = 0.6; ctx.stroke(); }
        ctx.restore();
    }

    function drawBackground(ctx, W, H) {
        const g = ctx.createLinearGradient(0, 0, 0, H);
        g.addColorStop(0, C.sky1); g.addColorStop(1, C.sky2);
        ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
        const S = 5;
        face(ctx, [iso(-S,0,0), iso(0,0,-S), iso(S,0,0), iso(0,0,S)], C.ground);
        ctx.save(); ctx.strokeStyle = C.grid; ctx.lineWidth = 0.5;
        for (let i = -S; i <= S; i++) {
            ctx.beginPath(); ctx.moveTo(iso(i,0,-S).x,iso(i,0,-S).y);
            ctx.lineTo(iso(i,0,S).x,iso(i,0,S).y); ctx.stroke();
            ctx.beginPath(); ctx.moveTo(iso(-S,0,i).x,iso(-S,0,i).y);
            ctx.lineTo(iso(S,0,i).x,iso(S,0,i).y); ctx.stroke();
        }
        ctx.restore();
    }

    function drawFoundation(ctx) {
        const FH = 0.4;
        for (let z = GZ-1; z >= 0; z--) for (let x = 0; x < GX; x++) {
            face(ctx, [iso(x,FH,z),iso(x+1,FH,z),iso(x+1,FH,z+1),iso(x,FH,z+1)], C.foundTop, '#0d1117');
            if (z === GZ-1) face(ctx, [iso(x,0,z+1),iso(x+1,0,z+1),iso(x+1,FH,z+1),iso(x,FH,z+1)], C.foundFront, '#0d1117');
            if (x === GX-1) face(ctx, [iso(x+1,0,z),iso(x+1,0,z+1),iso(x+1,FH,z+1),iso(x+1,FH,z)], C.foundSide, '#0d1117');
        }
    }

    function drawStructure(ctx, floors) {
        const FH = 0.4, cW = 0.07, wallH = 0.92;
        for (let fl = 0; fl < floors; fl++) {
            const by = FH + fl, sy = by + wallH;
            for (let z = GZ-1; z >= 0; z--) for (let x = 0; x < GX; x++) {
                face(ctx, [iso(x,sy,z),iso(x+1,sy,z),iso(x+1,sy,z+1),iso(x,sy,z+1)], C.slabTop, '#0d1117');
                if (z === GZ-1) face(ctx, [iso(x,sy,z+1),iso(x+1,sy,z+1),iso(x+1,sy+0.08,z+1),iso(x,sy+0.08,z+1)], C.slabFront, '#0d1117');
                if (x === GX-1) face(ctx, [iso(x+1,sy,z),iso(x+1,sy,z+1),iso(x+1,sy+0.08,z+1),iso(x+1,sy+0.08,z)], C.slabSide, '#0d1117');
            }
            for (let z = 0; z <= GZ; z++) for (let x = 0; x <= GX; x++) {
                face(ctx, [iso(x-cW,by,z+cW),iso(x+cW,by,z+cW),iso(x+cW,by+wallH,z+cW),iso(x-cW,by+wallH,z+cW)], C.colFront);
                face(ctx, [iso(x+cW,by,z-cW),iso(x+cW,by,z+cW),iso(x+cW,by+wallH,z+cW),iso(x+cW,by+wallH,z-cW)], C.colSide);
                face(ctx, [iso(x-cW,by+wallH,z-cW),iso(x+cW,by+wallH,z-cW),iso(x+cW,by+wallH,z+cW),iso(x-cW,by+wallH,z+cW)], C.colTop);
            }
        }
    }

    function drawMEP(ctx, floors) {
        const FH = 0.4;
        for (let fl = 0; fl < floors; fl++) {
            const midY = FH + fl + 0.55;
            ctx.save(); ctx.globalAlpha = 0.75;
            ctx.strokeStyle = C.duct; ctx.lineWidth = 5;
            const dA = iso(0.3,midY,1.5), dB = iso(2.7,midY,1.5);
            ctx.beginPath(); ctx.moveTo(dA.x,dA.y); ctx.lineTo(dB.x,dB.y); ctx.stroke();
            ctx.strokeStyle = C.pipe; ctx.lineWidth = 3;
            const pA = iso(1.5,midY-0.1,0.3), pB = iso(1.5,midY-0.1,2.7);
            ctx.beginPath(); ctx.moveTo(pA.x,pA.y); ctx.lineTo(pB.x,pB.y); ctx.stroke();
            ctx.restore();
        }
    }

    function drawCladding(ctx, floors) {
        const FH = 0.4, wallH = 0.92;
        for (let fl = 0; fl < floors; fl++) {
            const by = FH + fl;
            for (let x = 0; x < GX; x++) {
                face(ctx, [iso(x,by,GZ),iso(x+1,by,GZ),iso(x+1,by+wallH,GZ),iso(x,by+wallH,GZ)], C.wallFront, '#c8cdd1');
                const [wx1,wx2,wy1,wy2] = [x+0.15, x+0.85, by+0.22, by+0.72];
                face(ctx, [iso(wx1,wy1,GZ),iso(wx2,wy1,GZ),iso(wx2,wy2,GZ),iso(wx1,wy2,GZ)], C.winFill, null, 0.85);
                ctx.save(); ctx.strokeStyle = C.winBorder; ctx.lineWidth = 1; ctx.globalAlpha = 0.9;
                ctx.beginPath();
                [iso(wx1,wy1,GZ),iso(wx2,wy1,GZ),iso(wx2,wy2,GZ),iso(wx1,wy2,GZ)]
                    .forEach((p,i) => i===0 ? ctx.moveTo(p.x,p.y) : ctx.lineTo(p.x,p.y));
                ctx.closePath(); ctx.stroke(); ctx.restore();
            }
            for (let z = GZ-1; z >= 0; z--) {
                face(ctx, [iso(GX,by,z),iso(GX,by,z+1),iso(GX,by+wallH,z+1),iso(GX,by+wallH,z)], C.wallSide, '#b8bcbf');
                const [wz1,wz2,wy1,wy2] = [z+0.15, z+0.85, by+0.22, by+0.72];
                face(ctx, [iso(GX,wy1,wz1),iso(GX,wy1,wz2),iso(GX,wy2,wz2),iso(GX,wy2,wz1)], C.winFill, null, 0.85);
            }
        }
    }

    function drawRoof(ctx) {
        const topY = 0.4 + FLOORS, pH = 0.14;
        for (let z = GZ-1; z >= 0; z--) for (let x = 0; x < GX; x++)
            face(ctx, [iso(x,topY,z),iso(x+1,topY,z),iso(x+1,topY,z+1),iso(x,topY,z+1)], C.roofTop, '#0d1117');
        for (let x = 0; x < GX; x++)
            face(ctx, [iso(x,topY,GZ),iso(x+1,topY,GZ),iso(x+1,topY+pH,GZ),iso(x,topY+pH,GZ)], C.roofFront, '#0d1117');
        for (let z = GZ-1; z >= 0; z--)
            face(ctx, [iso(GX,topY,z),iso(GX,topY,z+1),iso(GX,topY+pH,z+1),iso(GX,topY+pH,z)], C.roofSide, '#0d1117');
    }

    // Which layers to draw for each BIM phase
    function layersFor(phaseKey, overallPct) {
        const p = overallPct / 100;
        return ({
            foundation: { foundation:true, structure:0,      mep:0,      cladding:0,      roof:false },
            structure:  { foundation:true, structure:FLOORS,  mep:0,      cladding:0,      roof:false },
            mep:        { foundation:true, structure:FLOORS,  mep:FLOORS, cladding:0,      roof:false },
            cladding:   { foundation:true, structure:FLOORS,  mep:FLOORS, cladding:FLOORS, roof:false },
            finishing:  { foundation:true, structure:FLOORS,  mep:FLOORS, cladding:FLOORS, roof:true  },
            all: {
                foundation: true,
                structure:  Math.min(FLOORS, Math.ceil(p * FLOORS * 1.5)),
                mep:        Math.min(FLOORS, Math.ceil(p * FLOORS * 1.1)),
                cladding:   Math.min(FLOORS, Math.ceil(p * FLOORS * 0.8)),
                roof:       p > 0.85,
            },
        })[phaseKey] || { foundation:true, structure:FLOORS, mep:0, cladding:0, roof:false };
    }

    return {
        render(canvas, phaseKey, overallPct) {
            const dpr = window.devicePixelRatio || 1;
            const W   = canvas.offsetWidth  || 600;
            const H   = canvas.offsetHeight || 380;
            canvas.width  = W * dpr;
            canvas.height = H * dpr;
            const ctx = canvas.getContext("2d");
            ctx.scale(dpr, dpr);
            _cx = W * 0.42; _cy = H * 0.58;
            const L = layersFor(phaseKey, overallPct);
            drawBackground(ctx, W, H);
            if (L.foundation)    drawFoundation(ctx);
            if (L.structure > 0) drawStructure(ctx, L.structure);
            if (L.mep > 0)       drawMEP(ctx, L.mep);
            if (L.cladding > 0)  drawCladding(ctx, L.cladding);
            if (L.roof)          drawRoof(ctx);
        },
    };
})();

/* ================================================================== */
/*  1. 3D Model Widget  (UC-02)                                         */
/* ================================================================== */

let _allPhases = [];   // cache of phase data from last API response

async function loadModelData(filter = "all") {
    try {
        const qs   = ACTIVE_PROJECT_ID
            ? `?filter=${filter}&project_id=${encodeURIComponent(ACTIVE_PROJECT_ID)}`
            : `?filter=${filter}`;
        const json = await fetch(`/api/dashboard/3d-model${qs}`).then(r => r.json());

        // Rebuild dropdown from real phase data on first load or project switch
        const sel = $("timeFilter");
        if (sel && json.filters?.length) {
            const prev = sel.value;
            sel.innerHTML = "";
            json.filters.forEach(f => {
                const opt = document.createElement("option");
                opt.value = f.value; opt.textContent = f.label;
                if (f.value === filter) opt.selected = true;
                sel.appendChild(opt);
            });
            if (!sel.value && prev) sel.value = prev;
        }
        _allPhases = json.filters || [];

        // BIM canvas is now owned by Three.js/IFCViewer — skip 2D render

        // Update phase tag overlay
        const pd  = json.phase_data || {};
        const tag = $("modelPhaseTag");
        if (tag) tag.textContent = `${pd.label || "All Phases"} — ${pd.completion ?? json.overall_pct ?? 0}% Complete`;

        // Rebuild BIM legend
        const legend = $("bimLegend");
        if (legend) {
            legend.innerHTML = (_allPhases.filter(f => f.value !== "all")).map(f =>
                `<div class="bim-legend-item">
                    <div class="bim-legend-dot" style="background:${f.color}"></div>
                    <span>${f.label} · ${f.completion}%</span>
                 </div>`
            ).join("");
        }

        // Sync phase marker on the progress graph
        _syncPhaseMarker(filter);
        // NOTE: #completionPct badge is owned exclusively by loadProgressData() — never written here.

        // Filter Three.js IFC meshes to match the BIM phase dropdown
        if (window.IFCViewer && typeof window.IFCViewer.setPhaseFilter === "function") {
            window.IFCViewer.setPhaseFilter(filter || "all");
        }

    } catch (err) {
        console.error("[BIM] Load error:", err);
        const tag = $("modelPhaseTag");
        if (tag) tag.textContent = "BIM data unavailable";
    }
}

/** Show an amber dashed marker on the SVG graph at the x-position of the selected phase. */
function _syncPhaseMarker(filter) {
    const marker = document.getElementById("phaseMarker");
    const dot    = document.getElementById("phaseMarkerDot");
    if (!marker || !dot) return;

    if (filter === "all") {
        marker.setAttribute("opacity", "0");
        dot.setAttribute("opacity", "0");
        return;
    }

    const ORDER = ["foundation", "structure", "mep", "cladding", "finishing"];
    const idx   = ORDER.indexOf(filter);
    if (idx < 0) return;

    const gW = 300, gH = 130, pad = 22;
    const x  = pad + idx * ((gW - 2 * pad) / (ORDER.length - 1));
    const pd  = _allPhases.find(f => f.value === filter) || {};
    const y  = gH - ((pd.completion ?? 50) / 100) * (gH - 10) + 10;

    marker.setAttribute("x1", x); marker.setAttribute("x2", x);
    marker.setAttribute("y1", 0); marker.setAttribute("y2", gH + 20);
    marker.setAttribute("opacity", "0.85");
    dot.setAttribute("cx", x); dot.setAttribute("cy", y);
    dot.setAttribute("opacity", "1");
}

// Attach listeners once DOM is ready
document.addEventListener("DOMContentLoaded", () => {
    const sel = $("timeFilter");
    if (sel) sel.addEventListener("change", e => loadModelData(e.target.value));

    const btn = $("modelInfoBtn");
    if (btn) btn.addEventListener("click", () => {
        const phases = _allPhases.filter(f => f.value !== "all");
        showToast(
            phases.length
                ? "BIM Phases — " + phases.map(f => `${f.label}: ${f.completion}%`).join(" | ")
                : "Select a project to load BIM phase data.",
            "info"
        );
    });

    let _resizeTimer = null;
    window.addEventListener("resize", () => {
        clearTimeout(_resizeTimer);
        _resizeTimer = setTimeout(() => {
            const sel = $("timeFilter");
            if (sel?.value) loadModelData(sel.value);
        }, 500);
    });

    const saveBtn = $("summaryBudgetSaveBtn");
    if (saveBtn) saveBtn.addEventListener("click", saveBudgetSpent);

    const spentInput = $("summaryBudgetSpentInput");
    if (spentInput) {
        spentInput.addEventListener("keydown", e => {
            if (e.key === "Enter") {
                e.preventDefault();
                saveBudgetSpent();
            }
        });
    }
});

/* ================================================================== */
/*  2. Project Progress Graph                                           */
/* ================================================================== */

function buildProgressPaths(values, targets, gW = 300, gH = 105, maxVal = 100) {
    const n  = values.length;
    const xs = values.map((_, i) => 22 + i * ((gW - 44) / (n - 1)));
    const yOf = v => gH - (v / maxVal) * gH + 10;

    const ptsA = xs.map((x, i) => `${x},${yOf(values[i])}`).join(" L ");
    const ptsT = xs.map((x, i) => `${x},${yOf(targets[i])}`).join(" L ");
    const base = `L ${xs[n-1]},${gH + 10} L ${xs[0]},${gH + 10} Z`;

    return {
        line:       `M ${ptsA}`,
        area:       `M ${ptsA} ${base}`,
        targetLine: `M ${ptsT}`,
        targetArea: `M ${ptsT} ${base}`,
    };
}

/** Fetch progress data for the ACTIVE project and render the SVG graph. */
async function loadProgressData() {
    try {
        const json  = await apiFetch("/api/dashboard/progress");
        const d     = json.data;
        const paths = buildProgressPaths(d.values, d.target);

        const set = (id, attr, val) => { const el = $(id); if (el) el.setAttribute(attr, val); };
        set("graphLine",  "d", paths.line);
        set("graphArea",  "d", paths.area);
        set("targetLine", "d", paths.targetLine);
        set("targetPath", "d", paths.targetArea);

        const badge = $("completionPct");
        if (badge) badge.textContent = `${d.current_completion}%`;

    } catch (err) {
        console.error("[Progress] Load error:", err);
        const badge = $("completionPct");
        if (badge) badge.textContent = "—";
    }
}

/* ================================================================== */
/*  3. Safety Alerts  — ACTIVE PROJECT only                            */
/* ================================================================== */

function severityIcon(severity) {
    const cls =
        severity === "critical" ? "critical" : severity === "medium" ? "medium" : "warning";
    return `<div class="alert-icon ${cls}">⚠</div>`;
}

function renderAlerts(alerts) {
    const list  = $("alertList");
    const badge = $("alertCountBadge");
    const bell  = $("bellBadge");
    if (!list) return;

    const critN  = alerts.filter(a => a.severity === "critical").length;
    const totalN = alerts.length;

    // Badge: show total count, hide when zero
    if (badge) {
        if (totalN > 0) {
            badge.textContent   = `${totalN} Alert${totalN !== 1 ? "s" : ""}`;
            badge.style.display = "inline-block";
        } else {
            badge.style.display = "none";
        }
    }

    // Bell notification dot
    if (bell) {
        bell.textContent   = critN;
        bell.style.display = critN > 0 ? "flex" : "none";
    }

    if (!totalN) {
        list.innerHTML = `<p class="text-muted" style="font-size:0.85rem;padding:0.5rem 0;">No active alerts — site is clear.</p>`;
        return;
    }

    // UC-03: each alert row is clickable — navigates to Safety Module
    // with the alert pre-highlighted (?alert=<id>)
    list.innerHTML = alerts.slice(0, 4).map(a => {
        const acked    = ackedAlerts.has(a.id);
        const isCrit   = a.severity === "critical";
        const isMed    = a.severity === "medium";
        const rowClass =
            isCrit && !acked ? "alert-item critical-row"
            : isMed && !acked ? "alert-item medium-row"
            : "alert-item";
        const safetyUrl = `/safety?alert=${encodeURIComponent(a.id)}&project_id=${encodeURIComponent(ACTIVE_PROJECT_ID)}`;
        return `
        <div class="${rowClass}" id="alert-${a.id}" data-url="${safetyUrl}" role="button" tabindex="0" title="View in Safety Monitor">
            ${severityIcon(a.severity)}
            <div class="alert-body">
                <div class="alert-title">${a.title}</div>
                <div class="alert-meta">${a.zone} · ${a.timestamp}</div>
            </div>
            <button class="alert-ack-btn ${acked ? "acked" : ""}"
                    data-id="${a.id}" ${acked ? "disabled" : ""}>
                ${acked ? "✓" : "Ack"}
            </button>
        </div>`;
    }).join("");

    // UC-03: click row body → navigate to Safety Module
    list.querySelectorAll(".alert-item").forEach(row => {
        row.addEventListener("click", e => {
            // Don't navigate if the Ack button was clicked
            if (e.target.closest(".alert-ack-btn")) return;
            window.location.href = row.dataset.url;
        });
        row.addEventListener("keydown", e => {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                window.location.href = row.dataset.url;
            }
        });
    });

    // Ack button handler
    list.querySelectorAll(".alert-ack-btn:not(.acked)").forEach(btn => {
        btn.addEventListener("click", e => {
            e.stopPropagation();
            acknowledgeAlert(btn.dataset.id);
        });
    });
}

/** Fetch alerts for the ACTIVE project only. */
async function loadAlerts() {
    try {
        const json = await apiFetch("/api/dashboard/alerts");
        renderAlerts(json.data);
    } catch (err) {
        console.error("[Alerts] Load error:", err);
    }
}

async function acknowledgeAlert(alertId) {
    try {
        await fetch(`/api/safety/alerts/${alertId}/acknowledge?project_id=${encodeURIComponent(ACTIVE_PROJECT_ID)}`, { method: "POST" });
        ackedAlerts.add(alertId);
        showToast(`Alert acknowledged.`, "success");
        loadAlerts();
    } catch {
        showToast("Could not acknowledge alert.", "error");
    }
}

const bellBtn = $("bellBtn");
if (bellBtn) bellBtn.addEventListener("click", () => $("alertList")?.scrollIntoView({ behavior: "smooth" }));

/* ================================================================== */
/*  4. Upcoming Tasks  — ACTIVE PROJECT only                           */
/* ================================================================== */

function countdownClass(days) {
    if (days <= 1) return "countdown-urgent";
    if (days <= 3) return "countdown-soon";
    return "countdown-ok";
}

function countdownLabel(days) {
    if (days <= 0) return "Due Today!";
    if (days === 1) return "1 Day";
    return `${days} Days`;
}

function statusEmoji(status) {
    return { in_progress: "🔧", pending: "⏳", scheduled: "📅", due_today: "🚨" }[status] ?? "📅";
}

/** Fetch tasks for the ACTIVE project only. */
async function loadTasks() {
    try {
        const json  = await apiFetch("/api/dashboard/tasks");
        const tasks = json.data;
        const badge = $("taskCountBadge");
        const list  = $("taskList");
        if (!list) return;

        if (badge) {
            badge.textContent   = `${tasks.length} Tasks`;
            badge.style.display = tasks.length > 0 ? "inline-flex" : "none";
        }

        if (!tasks.length) {
            list.innerHTML = `<p style="color:var(--text-secondary,#A0A0A0);font-size:0.85rem;padding:0.5rem 0;">No upcoming tasks scheduled for this project.</p>`;
            return;
        }

        list.innerHTML = tasks.slice(0, 4).map(t => `
        <div class="task-item">
            <div class="task-icon">${statusEmoji(t.status)}</div>
            <div class="task-details">
                <span class="task-name">${t.name}</span>
                <span class="task-status">Schedule ${t.schedule_pct}% Complete</span>
            </div>
            <span class="task-countdown ${countdownClass(t.days_remaining)}">
                ${countdownLabel(t.days_remaining)}
            </span>
        </div>`).join("");

    } catch (err) {
        console.error("[Tasks] Load error:", err);
        const list = $("taskList");
        if (list) list.innerHTML = `<p style="color:var(--text-secondary,#A0A0A0);font-size:0.85rem;padding:0.5rem 0;">Could not load tasks.</p>`;
    }
}

/* ================================================================== */
/*  5. VR Training  — logged-in USER on ACTIVE PROJECT                 */
/* ================================================================== */

function vrFillClass(status) {
    if (status === "passed")      return "fill-green";
    if (status === "in_progress") return "fill-blue";
    return "fill-grey";
}

function vrPctColour(status) {
    if (status === "passed")      return "var(--accent-green)";
    if (status === "in_progress") return "var(--accent-blue)";
    return "var(--text-secondary)";
}

/** Fetch VR modules for the CURRENT USER on the ACTIVE PROJECT. */
async function loadVrTraining() {
    try {
        const json = await apiFetch("/api/dashboard/vr-training");
        const badge = $("vrOverallBadge");
        const list  = $("vrList");
        if (!list) return;

        if (badge) {
            badge.textContent = json.mandatory_total > 0
                ? `${json.mandatory_done}/${json.mandatory_total} Mandatory`
                : `${json.overall_pct}% Avg`;
        }

        if (!json.data?.length) {
            list.innerHTML = `<p style="color:var(--text-secondary);font-size:0.82rem;">No VR modules assigned for this project.</p>`;
            return;
        }

        list.innerHTML = json.data.map(m => `
        <div class="vr-item">
            <div class="vr-header">
                <span class="truncate" style="max-width:65%">${m.title}</span>
                <span class="vr-pct" style="color:${vrPctColour(m.status)}">${m.completion}%</span>
            </div>
            <div class="progress-bg">
                <div class="progress-fill ${vrFillClass(m.status)}" style="width:${m.completion}%"></div>
            </div>
        </div>`).join("");

    } catch (err) {
        console.error("[VR Training] Load error:", err);
    }
}

/* ================================================================== */
/*  6. Team Roster                                                      */
/* ================================================================== */

const TEAM_ROLE_COLOURS = {
    "Lead Instructor":   { fg: "var(--accent-blue)",  bg: "rgba(74,144,226,0.1)"  },
    "Instructor / PM":   { fg: "var(--accent-blue)",  bg: "rgba(74,144,226,0.1)"  },
    "Safety Officer":    { fg: "var(--accent-red)",   bg: "rgba(229,115,115,0.1)" },
    "Student":           { fg: "var(--accent-green)", bg: "rgba(76,175,80,0.1)"   },
    "Site Foreman":      { fg: "var(--accent-orange)", bg: "rgba(255,152,0,0.12)" },
};

function renderTeamCard(member) {
    const role = TEAM_ROLE_COLOURS[member.role] ?? { fg: "var(--text-muted)", bg: "rgba(255,255,255,0.05)" };
    const avatar = member.avatar
        ? `<img src="${member.avatar}" alt="${member.name}">`
        : `<div style="width:100%;height:100%;background:#333;display:flex;align-items:center;justify-content:center;font-weight:700;color:#aaa;font-size:1rem">
               ${member.name.split(" ").map(n => n[0]).join("").slice(0, 2)}
           </div>`;
    return `
    <div class="team-card">
        <div class="team-avatar">${avatar}</div>
        <span class="team-name">${member.name}</span>
        <span class="team-role" style="color:${role.fg};background:${role.bg}">${member.role}</span>
    </div>`;
}

async function loadTeamRoster() {
    const container = $("teamRoster");
    if (!container) return;
    try {
        const json = await apiFetch("/api/project/team");
        const members = json.data || [];
        if (!members.length) {
            container.innerHTML = `<p style="color:var(--text-muted);font-size:0.85rem;padding:0.25rem 0;">No team members assigned for this project.</p>`;
            return;
        }
        container.innerHTML = members.map(renderTeamCard).join("");
    } catch (err) {
        console.error("[Team] Load error:", err);
        container.innerHTML = `<p style="color:var(--text-muted);font-size:0.85rem;padding:0.25rem 0;">Could not load team roster.</p>`;
    }
}

/* ================================================================== */
/*  7. Project Documents                                               */
/* ================================================================== */

const DOC_TYPE_STYLES = {
    PDF: { bg: "#D32F2F", label: "PDF" },
    DOC: { bg: "#1976D2", label: "DOC" },
    XLS: { bg: "#388E3C", label: "XLS" },
};

function renderDashboardDocItem(doc) {
    const style = DOC_TYPE_STYLES[doc.type] ?? { bg: "#555", label: (doc.type || "FILE").slice(0, 3).toUpperCase() };
    return `
        <div class="doc-item" data-id="${doc.id}" title="Download ${doc.name}">
            <div class="doc-icon" style="background:${style.bg}">${style.label}</div>
            <div class="doc-info">
                <span class="doc-name">${doc.name}</span>
                <span class="doc-meta">${doc.updated} · ${doc.size}</span>
            </div>
        </div>`;
}

async function loadDocumentsWidget() {
    const list = $("dashboardDocList");
    const badge = $("docCountBadge");
    if (!list) return;
    try {
        const json = await apiFetch("/api/project/documents");
        const docs = json.data || [];
        if (badge) badge.textContent = `${docs.length} Files`;
        if (!docs.length) {
            list.innerHTML = `<p style="color:var(--text-secondary,#A0A0A0);font-size:0.85rem;padding:0.5rem 0;">No project documents uploaded.</p>`;
            return;
        }
        list.innerHTML = docs.slice(0, 6).map(renderDashboardDocItem).join("");
        list.querySelectorAll(".doc-item").forEach(item => {
            item.addEventListener("click", () => {
                const name = item.querySelector(".doc-name")?.textContent || "document";
                showToast(`Opening ${name}…`, "info");
            });
        });
    } catch (err) {
        console.error("[Documents] Load error:", err);
        if (badge) badge.textContent = "—";
        list.innerHTML = `<p style="color:var(--text-secondary,#A0A0A0);font-size:0.85rem;padding:0.5rem 0;">Could not load documents.</p>`;
    }
}

/* ================================================================== */
/*  Real-time: Server-Sent Events                                       */
/* ================================================================== */

function connectSSE() {
    if (typeof EventSource === "undefined") return;

    const es = new EventSource("/api/events");
    es.onopen = () => console.log("[SSE] Connected.");

    es.onmessage = event => {
        try {
            const payload = JSON.parse(event.data);
            if (payload.type !== "dashboard_update") return;
            // SSE is a signal-only ping — fetch fresh real alert data from the API.
            // We never render payload.alerts directly: the broadcaster was pushing
            // mock SAFETY_ALERTS with wrong IDs, causing the widget to show stale
            // data and breaking the UC-03 deep-link into the Safety Monitor.
            loadAlerts();
            // #completionPct is owned exclusively by loadProgressData().
        } catch (e) { console.warn("[SSE] Parse error:", e); }
    };

    es.onerror = () => {
        es.close();
        setTimeout(connectSSE, 5000);
    };
}

/* ================================================================== */
/*  Initialisation                                                      */
/* ================================================================== */

async function initDashboard() {
    // Load project switcher first — sets ACTIVE_PROJECT_ID if not in URL
    await loadProjectSwitcher();

    // If the switcher found no store projects, ACTIVE_PROJECT_ID is still "".
    // Pass it empty so _resolve_project_id() on the server uses DEFAULT_PROJECT_ID.
    // This ensures widgets always get data regardless of store state.

    await syncIfcViewerWithActiveProject();

    // Load all widgets in parallel
    await Promise.allSettled([
        loadProjectSummary(),
        loadModelData("all"),
        loadProgressData(),
        loadAlerts(),
        loadTasks(),
        loadVrTraining(),
        loadDocumentsWidget(),
        loadTeamRoster(),
    ]);

    connectSSE();
    setInterval(loadAlerts,       30_000);
    setInterval(loadProgressData, 60_000);
}

document.addEventListener("DOMContentLoaded", initDashboard);