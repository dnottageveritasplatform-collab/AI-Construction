/**
 * resource_plan.js
 * ----------------
 * Wrapped in an IIFE so every declaration lives in a private scope.
 * This prevents name collisions with utils.js or any other globally-loaded
 * script, which would throw a SyntaxError in strict mode and silently kill
 * the entire file before a single line of our code runs.
 *
 * window.xxx assignments expose the functions that HTML onclick / ondragover
 * attributes need.
 */
(function () {
"use strict";


/* ================================================================== */
/*  Utilities                                                           */
/* ================================================================== */

let _toastTimer;

/**
 * Show a brief toast notification.
 * @param {string} msg
 * @param {"info"|"success"|"warning"|"error"} type
 */
function showToast(msg, type = "info") {
    const el = document.getElementById("toast");
    if (!el) return;
    el.textContent = msg;
    el.className = `show ${type}`;
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => { el.className = ""; }, 3200);
}

/* ================================================================== */
/*  Project context (shared localStorage with Dashboard / Safety)      */
/* ================================================================== */

const RP_USER_ID = "usr-001";
const _rpSP = new URLSearchParams(window.location.search);
let rpActiveProjectId =
    (window.VeritasProjectContext?.parseFromUrl(_rpSP)) ||
    (window.VeritasProjectContext?.readPersisted()) ||
    "";

function getRpTasksStorageKey() {
    return `veritas_rp_tasks_v1_${rpActiveProjectId || "default"}`;
}

/** Append ?project_id= for /api/dashboard/tasks when a project is active. */
function rpApiUrl(path) {
    const sep = path.includes("?") ? "&" : "?";
    return rpActiveProjectId
        ? `${path}${sep}project_id=${encodeURIComponent(rpActiveProjectId)}`
        : path;
}

/** Push Kanban column / % to server gantt row so Dashboard progress stays in sync. */
function syncKanbanTaskToServer(task) {
    if (!rpActiveProjectId || !task || task.id == null || task.id === "") return;
    const id = encodeURIComponent(String(task.id));
    fetch(rpApiUrl(`/api/dashboard/tasks/${id}`), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            status: task.status,
            schedule_pct: task.schedule_pct,
        }),
    }).catch(() => {});
}

/** Remove predecessor references to deletedId from in-memory Gantt rows (demo / no-server path). */
function _rpStripPredFromGanttTasks(ganttTasks, deletedId) {
    const rid = String(deletedId);
    for (const t of ganttTasks) {
        const deps = t.deps;
        if (!Array.isArray(deps) || deps.length === 0) continue;
        const lag = Array.isArray(t.dep_lag_wd) ? t.dep_lag_wd : [];
        const newDeps = [];
        const newLag = [];
        deps.forEach((d, i) => {
            if (String(d) !== rid) {
                newDeps.push(d);
                newLag.push(lag[i] != null ? lag[i] : 0);
            }
        });
        t.deps = newDeps;
        t.dep_lag_wd = newLag;
    }
}

/**
 * Delete a task from Kanban + Gantt (server when a project is active, else local).
 */
async function deleteTaskById(taskId) {
    if (taskId == null || taskId === "") return;
    const tid = String(taskId);
    const task = tasks.find(t => String(t.id) === tid);
    const label = task ? task.name : tid;
    if (!window.confirm(`Delete "${label}"? This cannot be undone.`)) return;

    if (rpActiveProjectId) {
        try {
            const res = await fetch(rpApiUrl(`/api/dashboard/tasks/${encodeURIComponent(tid)}`), { method: "DELETE" });
            const json = await res.json().catch(() => ({}));
            if (!res.ok) {
                showToast(json.message || "Could not delete task.", "error");
                return;
            }
            if (String(activeModalId) === tid) closeModal();
            rpCloseGanttTaskEditModal();
            await syncTasksFromAPI();
            await syncRpGanttFromAPI();
            showToast("Task deleted.", "success");
        } catch {
            showToast("Could not delete task.", "error");
        }
        return;
    }

    tasks = tasks.filter(t => String(t.id) !== tid);
    rpGanttTasks = rpGanttTasks.filter(t => String(t.id) !== tid);
    _rpStripPredFromGanttTasks(rpGanttTasks, tid);
    saveTasks();
    renderBoard();
    renderRpGantt();
    if (String(activeModalId) === tid) closeModal();
    rpCloseGanttTaskEditModal();
    showToast("Task removed.", "success");
}

function deleteTaskFromModal() {
    if (!activeModalId) return;
    deleteTaskById(activeModalId);
}

async function loadResourcePlanProjectSwitcher() {
    if (!window.VeritasProjectContext) return;
    const wrap = document.getElementById("projectSwitcher");
    if (!wrap) return;
    try {
        const projects = await VeritasProjectContext.fetchProjectsList(RP_USER_ID);
        if (!projects.length) return;

        rpActiveProjectId = VeritasProjectContext.resolveActiveId(projects, rpActiveProjectId);
        if (!rpActiveProjectId) return;

        window.VeritasProjectContext.writePersisted(rpActiveProjectId);
        const url = new URL(window.location.href);
        url.searchParams.set("project_id", rpActiveProjectId);
        window.history.replaceState({}, "", url);

        wrap.innerHTML = `
            <select id="projectSelect" onchange="switchResourcePlanProject(this.value)"
                style="background:var(--bg-card);color:var(--text-main);
                       border:1px solid var(--border);padding:6px 12px;
                       border-radius:8px;font-size:0.85rem;cursor:pointer;min-width:220px;">
                ${projects.map(p => `
                    <option value="${p.id}" ${p.id === rpActiveProjectId ? "selected" : ""}>
                        ${p.name} · ${p.completion}%
                    </option>
                `).join("")}
            </select>
        `;
    } catch (e) {
        console.warn("[Resource Plan Switcher] Could not load projects:", e);
    }
}

async function switchResourcePlanProject(projectId) {
    rpActiveProjectId = projectId;
    window.VeritasProjectContext?.writePersisted(projectId);
    const sel = document.getElementById("projectSelect");
    if (sel && sel.value !== projectId) sel.value = projectId;
    const url = new URL(window.location.href);
    url.searchParams.set("project_id", projectId);
    window.history.replaceState({}, "", url);
    updateResourcePlanNavLinks();
    tasks = loadTasks();
    renderBoard();
    await syncTasksFromAPI();
    await syncRpGanttFromAPI();
    showToast("Switched project context.", "info");
}

function updateResourcePlanNavLinks() {
    const pid = encodeURIComponent(rpActiveProjectId);
    const home = document.getElementById("navLinkHome");
    const recent = document.getElementById("navLinkRecentAlerts");
    const rp = document.getElementById("navLinkResourcePlan");
    const vr = document.getElementById("navLinkVrTraining");
    const rl = document.getElementById("navLinkResourciist");
    if (home) {
        home.href = rpActiveProjectId ? `/dashboard?project=${pid}` : "/dashboard";
    }
    if (recent) {
        recent.href = rpActiveProjectId ? `/safety?project_id=${pid}` : "/safety";
    }
    if (rp) {
        rp.href = rpActiveProjectId ? `/resource-plan?project_id=${pid}` : "/resource-plan";
    }
    if (vr) {
        vr.href = rpActiveProjectId ? `/vr-training?project_id=${pid}` : "/vr-training";
    }
    if (rl) {
        rl.href = rpActiveProjectId ? `/resourciist?project_id=${pid}` : "/resourciist";
    }
}

window.switchResourcePlanProject = switchResourcePlanProject;

/* ================================================================== */
/*  Task Data  (UC-04 — static seed, overridden by API on init)        */
/* ================================================================== */

const TASK_SEED = [
    {
        id: "t1", name: "Concrete Pouring",
        desc: "Pour foundation slab for Zones 2–4. Ensure correct mix ratio (Type S) and adequate curing time before next phase begins.",
        status: "in_progress", schedule_pct: 85, days_remaining: 3,
        priority: "high", assignees: ["DN", "JS"], category: "Foundation",
    },
    {
        id: "t2", name: "Steel Beam Delivery",
        desc: "Coordinate logistics for I-beam delivery from supplier. Confirm crane availability and receiving zone clearance.",
        status: "scheduled", schedule_pct: 40, days_remaining: 2,
        priority: "high", assignees: ["SL"], category: "Logistics",
    },
    {
        id: "t3", name: "Site Preparation — Zone B",
        desc: "Clear debris, level ground, and mark survey pins for the east wing expansion footprint.",
        status: "completed", schedule_pct: 100, days_remaining: 0,
        priority: "low", assignees: ["AJ", "MW"], category: "Site Work",
    },
    {
        id: "t4", name: "Structural Assembly — Frame",
        desc: "Erect steel frame for floors 2–3 using delivered beams. Safety harness required. Engineer sign-off needed.",
        status: "scheduled", schedule_pct: 10, days_remaining: 7,
        priority: "high", assignees: ["DN", "JS", "AJ"], category: "Structural",
    },
    {
        id: "t5", name: "Safety Inspection — Walkthrough",
        desc: "Mandatory weekly site walkthrough by Safety Officer. Checklist includes PPE compliance, scaffolding stability, and fire exits.",
        status: "review", schedule_pct: 70, days_remaining: 1,
        priority: "high", assignees: ["SL"], category: "Safety",
    },
    {
        id: "t6", name: "Electrical Rough-In — Level 1",
        desc: "Install conduit runs and junction boxes per approved electrical plan. Coordinate with structural to avoid conflicts.",
        status: "scheduled", schedule_pct: 5, days_remaining: 10,
        priority: "med", assignees: ["MW"], category: "MEP",
    },
    {
        id: "t7", name: "Plumbing Rough-In",
        desc: "Run supply and waste lines through open framing before wall close-in. Pressure test required prior to inspection.",
        status: "scheduled", schedule_pct: 0, days_remaining: 12,
        priority: "med", assignees: ["JS"], category: "MEP",
    },
    {
        id: "t8", name: "Quality Control Audit",
        desc: "Third-party QC review of all completed foundation and structural work. Documentation to be uploaded to project module.",
        status: "review", schedule_pct: 60, days_remaining: 4,
        priority: "med", assignees: ["DN", "SL"], category: "QA / QC",
    },
    {
        id: "t9", name: "Material Procurement — Phase 2",
        desc: "Issue POs for roofing, insulation, and window systems. Confirm lead times with project timeline.",
        status: "completed", schedule_pct: 100, days_remaining: 0,
        priority: "low", assignees: ["AJ"], category: "Procurement",
    },
];

/** Demo seed card ids — never merge these when syncing a real project from the API */
const TASK_SEED_IDS = new Set(TASK_SEED.map(t => String(t.id)));

/** Kanban column ids — must match HTML col-* / cards-* ids */
const KANBAN_COLUMN_STATUSES = new Set(["scheduled", "in_progress", "review", "completed"]);

/**
 * Map dashboard API / legacy task.status values onto Kanban column ids.
 * API uses due_today, pending, etc.; Kanban only recognises four columns.
 */
function normalizeTaskForKanban(task) {
    const out = { ...task };
    const pct = Math.min(100, Math.max(0, Number(out.schedule_pct) || 0));
    out.schedule_pct = pct;
    let s = String(out.status || "").toLowerCase();

    const allowFullNonDone = s === "review" || s === "in_progress" || s === "scheduled";
    if (s === "completed" || (pct >= 100 && !allowFullNonDone)) {
        out.status = "completed";
        if (pct >= 100) out.schedule_pct = 100;
        return out;
    }
    if (s === "due_today") {
        out.status = "in_progress";
        return out;
    }
    if (s === "pending") {
        out.status = "scheduled";
        return out;
    }
    if (KANBAN_COLUMN_STATUSES.has(s)) {
        out.status = s;
        return out;
    }
    out.status = "scheduled";
    return out;
}

function normalizeTaskList(arr) {
    return (arr || []).map(t => normalizeTaskForKanban({ ...t }));
}

// ── localStorage persistence (scoped per active project) ─────────────

/**
 * Write the current tasks array to localStorage so it survives
 * page refreshes.  Called after every mutation.
 */
function saveTasks() {
    try {
        localStorage.setItem(getRpTasksStorageKey(), JSON.stringify(tasks));
    } catch (e) { /* storage quota / private-browsing — silently ignore */ }
}

/**
 * Return saved tasks from localStorage. For a scoped project, do not fall back
 * to TASK_SEED (avoids Kanban/Gantt mismatch); the board fills from GET ?full=1.
 * Without a project, use TASK_SEED when nothing is saved.
 */
function loadTasks() {
    try {
        const raw = localStorage.getItem(getRpTasksStorageKey());
        if (raw) {
            const parsed = JSON.parse(raw);
            if (Array.isArray(parsed) && parsed.length > 0) return normalizeTaskList(parsed);
        }
    } catch (e) { /* corrupt JSON — fall through */ }
    if (rpActiveProjectId) return [];
    return normalizeTaskList(JSON.parse(JSON.stringify(TASK_SEED)));
}

// ── Mutable runtime state ────────────────────────────────────────────
let tasks                = loadTasks();   // ← restores persisted tasks on load
let dragId               = null;
let activeModalId        = null;
let activePriorityFilter = "all";
let searchQuery          = "";

/* ================================================================== */
/*  Kanban Board Renderer                                               */
/* ================================================================== */

const CATEGORY_ICONS = {
    "Foundation":  "🏗️",
    "Logistics":   "🚛",
    "Site Work":   "⛏️",
    "Structural":  "🔩",
    "Safety":      "⚠️",
    "MEP":         "⚡",
    "QA / QC":     "✅",
    "Procurement": "📦",
};

/** Re-render all four Kanban columns based on current state. */
function renderBoard() {
    ["scheduled", "in_progress", "review", "completed"].forEach(colId => {
        const container = document.getElementById(`cards-${colId}`);
        const countEl   = document.getElementById(`count-${colId}`);
        if (!container || !countEl) return;

        container.innerHTML = "";

        const colTasks = tasks.filter(t => {
            if (t.status !== colId) return false;
            if (activePriorityFilter !== "all" && t.priority !== activePriorityFilter) return false;
            if (searchQuery && !t.name.toLowerCase().includes(searchQuery)) return false;
            return true;
        });

        countEl.textContent = colTasks.length;
        colTasks.forEach(task => container.appendChild(buildCard(task)));
    });
}

/** Build a single draggable task card DOM element. */
function buildCard(task) {
    const card = document.createElement("div");
    card.className = "task-card";
    card.setAttribute("draggable", "true");
    card.dataset.id = task.id;

    card.addEventListener("dragstart", e => onDragStart(e, task.id));
    card.addEventListener("dragend",   onDragEnd);
    card.addEventListener("click",     () => openModal(task.id));

    const prioClass = { high:"prio-high", med:"prio-med", low:"prio-low" }[task.priority];
    const prioLabel = { high:"High",      med:"Medium",   low:"Low"      }[task.priority];

    const daysClass = task.status === "completed" ? "days-done"
                    : task.days_remaining <= 1    ? "days-urgent"
                    : task.days_remaining <= 4    ? "days-soon"
                    : "days-ok";

    const daysLabel = task.status === "completed" ? "Done"
                    : task.days_remaining === 0   ? "Due Today"
                    : task.days_remaining === 1   ? "1 Day Left"
                    : `${task.days_remaining} Days`;

    const barColor  = task.status === "completed" ? "var(--accent-green)"
                    : task.schedule_pct >= 80     ? "var(--accent-blue)"
                    : task.schedule_pct >= 40     ? "var(--accent-orange)"
                    : "var(--accent-red)";

    const catIcon   = CATEGORY_ICONS[task.category] || "📋";
    const avatars   = task.assignees.map(a => `<div class="mini-avatar">${a}</div>`).join("");

    const idJs = JSON.stringify(String(task.id));
    card.innerHTML = `
        <div class="task-top">
            <div class="task-name">${task.name}</div>
            <div class="task-top-actions">
                <button type="button" class="task-card-delete" title="Delete task" aria-label="Delete"
                    onclick="event.stopPropagation();window.deleteTaskById(${idJs})">×</button>
                <span class="priority-badge ${prioClass}">${prioLabel}</span>
            </div>
        </div>
        <div class="task-desc">${task.desc.substring(0, 80)}…</div>
        <div class="task-progress-wrap">
            <div class="task-progress-label">
                <span>Schedule</span>
                <span>${task.schedule_pct}%</span>
            </div>
            <div class="progress-track">
                <div class="progress-fill" style="width:${task.schedule_pct}%;background:${barColor};"></div>
            </div>
        </div>
        <div class="task-footer">
            <div class="task-meta">
                <div class="meta-pill">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
                    ${catIcon} ${task.category}
                </div>
            </div>
            <div style="display:flex;align-items:center;gap:0.5rem;">
                <div class="task-assignees">${avatars}</div>
                <span class="days-pill ${daysClass}">${daysLabel}</span>
            </div>
        </div>
    `;

    return card;
}

/* ================================================================== */
/*  Drag and Drop                                                       */
/* ================================================================== */

function onDragStart(e, id) {
    dragId = id;
    e.target.classList.add("drag-ghost");
    e.dataTransfer.effectAllowed = "move";
}

function onDragEnd(e) {
    e.target.classList.remove("drag-ghost");
    document.querySelectorAll(".kanban-col").forEach(c => c.classList.remove("drag-over"));
}

function onDragOver(e, colId) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    document.getElementById(`col-${colId}`).classList.add("drag-over");
}

function onDragLeave(e) {
    e.currentTarget.classList.remove("drag-over");
}

function onDrop(e, colId) {
    e.preventDefault();
    e.currentTarget.classList.remove("drag-over");
    if (!dragId) return;

    const task = tasks.find(t => String(t.id) === String(dragId));
    if (task && task.status !== colId) {
        const wasCompleted = task.status === "completed";
        task.status = colId;
        if (colId === "completed") {
            task.schedule_pct = 100;
        } else if (wasCompleted) {
            /* Leave Complete: drop % so server pct_complete matches column (budget uses status only). */
            const p = Number(task.schedule_pct) || 0;
            task.schedule_pct = p >= 100 ? 99 : Math.max(0, Math.min(99, Math.round(p)));
        }
        saveTasks();
        syncKanbanTaskToServer(task);
        showToast(`"${task.name}" moved to ${colId.replace("_", " ")}`, "success");
        renderBoard();
    }
    dragId = null;
}

// Expose drag handlers to HTML ondragover / ondrop attributes
window.onDragOver  = onDragOver;
window.onDragLeave = onDragLeave;
window.onDrop      = onDrop;

/* ================================================================== */
/*  Task Detail / Edit Modal                                            */
/* ================================================================== */

function syncRpKanbanInspectionFields() {
    const cb = document.getElementById("ed-inspection-needed");
    const dateEl = document.getElementById("ed-inspection-date");
    const lbl = document.getElementById("ed-inspection-date-label");
    if (!cb || !dateEl) return;
    const on = cb.checked;
    dateEl.disabled = !on;
    if (!on) dateEl.value = "";
    if (lbl) lbl.style.opacity = on ? "1" : "0.5";
}

function syncRpGanttInspectionFields() {
    const cb = document.getElementById("rpGanttEditInspectionNeeded");
    const dateEl = document.getElementById("rpGanttEditInspectionDate");
    const lbl = document.getElementById("rpGanttEditInspectionDateLabel");
    if (!cb || !dateEl) return;
    const on = cb.checked;
    dateEl.disabled = !on;
    if (!on) dateEl.value = "";
    if (lbl) lbl.style.opacity = on ? "1" : "0.5";
}

/**
 * Populate the view-mode <p> elements and open the modal.
 * Always opens in view mode (editing class removed).
 */
function openModal(id) {
    const task = tasks.find(t => t.id === id);
    if (!task) return;
    activeModalId = id;

    // Ensure we start in view mode
    document.getElementById("taskModalInner").classList.remove("editing");

    // Populate view-mode display fields
    document.getElementById("modalTitle").textContent    = task.name;
    document.getElementById("modalDesc").textContent     = task.desc;
    document.getElementById("modalStatus").textContent   = `Schedule ${task.schedule_pct}% Complete`;
    document.getElementById("modalDays").textContent     = task.status === "completed" ? "Completed ✓" : `${task.days_remaining} day(s)`;
    document.getElementById("modalPriority").textContent = { high:"High 🔴", med:"Medium 🟡", low:"Low 🟢" }[task.priority];
    document.getElementById("modalAssigned").textContent = task.assignees.join(", ");
    document.getElementById("modalProgressBar").style.width = `${task.schedule_pct}%`;

    const sd = task.start_date ? String(task.start_date).slice(0, 10) : "";
    document.getElementById("modalStart").textContent = sd || "—";
    const dwd = task.duration_wd != null && task.duration_wd !== "" && Number.isFinite(Number(task.duration_wd))
        ? String(Math.max(1, Math.round(Number(task.duration_wd))))
        : "—";
    document.getElementById("modalDurWd").textContent = dwd;

    const c = task.cost;
    document.getElementById("modalCost").textContent =
        c != null && c !== "" && Number.isFinite(Number(c)) ? String(Math.round(Number(c) * 100) / 100) : "—";

    const inspEl = document.getElementById("modalInspection");
    if (inspEl) {
        const ir = task.inspection_required;
        const needed = ir === true || ir === 1 || String(ir).toLowerCase() === "true";
        const idt = task.inspection_date ? String(task.inspection_date).slice(0, 10) : "";
        if (!needed) inspEl.textContent = "Not required";
        else inspEl.textContent = idt ? `Yes — ${idt}` : "Yes — date not set";
    }

    document.getElementById("taskModal").classList.add("open");
}

/**
 * Switch the modal into edit mode:
 * - Populates all edit-mode <input>/<select>/<textarea> fields from the task
 * - Adds "editing" class to the modal inner div, which CSS uses to swap
 *   .view-only → hidden and .edit-only → visible
 */
function enterEditMode() {
    const task = tasks.find(t => t.id === activeModalId);
    if (!task) return;

    document.getElementById("ed-name").value      = task.name;
    document.getElementById("ed-desc").value      = task.desc === "No description provided." ? "" : task.desc;
    document.getElementById("ed-pct").value       = task.schedule_pct;
    document.getElementById("ed-days").value      = task.days_remaining;
    document.getElementById("ed-priority").value  = task.priority;
    document.getElementById("ed-assignees").value = task.assignees.join(", ");
    document.getElementById("ed-category").value  = task.category;
    document.getElementById("ed-status").value    = task.status;
    document.getElementById("ed-start").value     = task.start_date ? String(task.start_date).slice(0, 10) : "";
    const dw = task.duration_wd != null ? Number(task.duration_wd) : NaN;
    document.getElementById("ed-duration-wd").value = Number.isFinite(dw) && dw >= 1 ? String(Math.round(dw)) : "";
    const costEl = document.getElementById("ed-cost");
    if (costEl) {
        costEl.value = task.cost != null && task.cost !== "" && Number.isFinite(Number(task.cost))
            ? String(Number(task.cost))
            : "";
    }
    const inspCb = document.getElementById("ed-inspection-needed");
    const inspDate = document.getElementById("ed-inspection-date");
    if (inspCb) {
        const ir = task.inspection_required;
        inspCb.checked = ir === true || ir === 1 || String(ir).toLowerCase() === "true";
    }
    if (inspDate) {
        const id = task.inspection_date;
        inspDate.value = id ? String(id).slice(0, 10) : "";
    }
    syncRpKanbanInspectionFields();

    // Clear any leftover validation state
    document.getElementById("ed-name").classList.remove("field-error");
    document.getElementById("ed-name-err").classList.remove("visible");

    document.getElementById("taskModalInner").classList.add("editing");
    setTimeout(() => document.getElementById("ed-name").focus(), 60);
}

/** Return to view mode without saving (cancel). */
function exitEditMode() {
    document.getElementById("taskModalInner").classList.remove("editing");
}

/**
 * Validate edit-mode fields, write the changes back into the tasks array,
 * refresh both the modal view and the Kanban board, and PATCH to the API.
 */
async function saveTaskEdits() {
    const nameEl = document.getElementById("ed-name");
    const name   = nameEl.value.trim();

    if (!name) {
        nameEl.classList.add("field-error");
        document.getElementById("ed-name-err").classList.add("visible");
        nameEl.focus();
        return;
    }
    nameEl.classList.remove("field-error");
    document.getElementById("ed-name-err").classList.remove("visible");

    const task = tasks.find(t => t.id === activeModalId);
    if (!task) return;

    const startVal = (document.getElementById("ed-start").value || "").trim().slice(0, 10);
    const durRaw   = (document.getElementById("ed-duration-wd").value || "").trim();
    const VG = window.VeritasGantt;
    if (startVal && rpGanttDetails.start_date && VG) {
        const ps = VG.parseISODateUTC(String(rpGanttDetails.start_date).slice(0, 10));
        const u = VG.parseISODateUTC(startVal);
        if (ps && u && VG.daysBetweenUTC(ps, u) < 0) {
            showToast("Start date cannot be before the project start.", "error");
            return;
        }
    }

    const rawAssignees = document.getElementById("ed-assignees").value;
    const assignees = rawAssignees
        .split(",")
        .map(s => s.trim().toUpperCase())
        .filter(s => s.length > 0);

    // Write all edited values back to the task object
    task.name           = name;
    task.desc           = document.getElementById("ed-desc").value.trim() || "No description provided.";
    task.schedule_pct   = Math.min(100, Math.max(0, parseInt(document.getElementById("ed-pct").value,  10) || 0));
    task.days_remaining = Math.max(0,   parseInt(document.getElementById("ed-days").value, 10) || 0);
    task.priority       = document.getElementById("ed-priority").value;
    task.assignees      = assignees.length ? assignees : task.assignees;
    task.category       = document.getElementById("ed-category").value;
    task.status         = document.getElementById("ed-status").value;
    if (startVal) task.start_date = startVal;
    else delete task.start_date;
    if (durRaw !== "") task.duration_wd = Math.max(1, parseInt(durRaw, 10) || 1);

    const costRaw = (document.getElementById("ed-cost")?.value || "").trim();
    const parsedCost = costRaw === "" ? null : Number(costRaw);
    if (costRaw !== "" && (!Number.isFinite(parsedCost) || parsedCost < 0)) {
        showToast("Task cost must be a non-negative number.", "error");
        return;
    }
    if (parsedCost == null) {
        delete task.cost;
    } else {
        task.cost = Math.round(parsedCost * 100) / 100;
    }

    const inspCb = document.getElementById("ed-inspection-needed");
    const inspDateEl = document.getElementById("ed-inspection-date");
    const inspectionNeeded = !!(inspCb && inspCb.checked);
    if (!inspectionNeeded) {
        delete task.inspection_required;
        delete task.inspection_date;
    } else {
        task.inspection_required = true;
        const idate = (inspDateEl && inspDateEl.value ? String(inspDateEl.value) : "").trim().slice(0, 10);
        if (idate) task.inspection_date = idate;
        else delete task.inspection_date;
    }

    // Snap completed tasks to 100 %
    if (task.status === "completed") task.schedule_pct = 100;
    saveTasks();

    if (rpActiveProjectId) {
        const body = {
            name:           task.name,
            desc:           task.desc,
            status:         task.status,
            schedule_pct:   task.schedule_pct,
            days_remaining: task.days_remaining,
            priority:       task.priority,
            category:       task.category,
        };
        if (startVal) body.start_date = startVal;
        if (durRaw !== "") body.duration_wd = task.duration_wd;
        if (parsedCost == null) body.cost = null;
        else body.cost = task.cost;
        if (!inspectionNeeded) {
            body.inspection_required = false;
            body.inspection_date = null;
        } else {
            body.inspection_required = true;
            body.inspection_date = task.inspection_date || null;
        }
        try {
            const res = await fetch(rpApiUrl(`/api/dashboard/tasks/${encodeURIComponent(String(task.id))}`), {
                method:  "PATCH",
                headers: { "Content-Type": "application/json" },
                body:    JSON.stringify(body),
            });
            const j = await res.json().catch(() => ({}));
            if (!res.ok) {
                showToast(j.message || "Could not save task.", "error");
                return;
            }
            await syncTasksFromAPI();
            await syncRpGanttFromAPI();
        } catch {
            showToast("Could not save task.", "error");
            return;
        }
    } else {
        syncKanbanTaskToServer(task);
    }

    // Switch back to view mode and refresh the view-mode display
    exitEditMode();
    openModal(activeModalId); // re-populates view fields with new values

    // Re-render the board so the card reflects the edits immediately
    renderBoard();
    showToast(`"${task.name}" updated`, "success");
}

function closeModal() {
    document.getElementById("taskModal").classList.remove("open");
    document.getElementById("taskModalInner").classList.remove("editing");
    activeModalId = null;
}

function markDone() {
    if (!activeModalId) return;
    const task = tasks.find(t => t.id === activeModalId);
    if (task) {
        task.status       = "completed";
        task.schedule_pct = 100;
        saveTasks();
        syncKanbanTaskToServer(task);
        showToast(`"${task.name}" marked as complete ✓`, "success");
        renderBoard();
    }
    closeModal();
}

// Expose modal handlers called from HTML attributes
window.openModal      = openModal;
window.closeModal     = closeModal;
window.markDone       = markDone;
window.enterEditMode  = enterEditMode;
window.exitEditMode   = exitEditMode;
window.saveTaskEdits  = saveTaskEdits;
window.deleteTaskById = deleteTaskById;
window.deleteTaskFromModal   = deleteTaskFromModal;

/* ================================================================== */
/*  Search and Filter                                                   */
/* ================================================================== */

/** Live search — called by input oninput handler in HTML. */
function filterCards(q) {
    searchQuery = q.toLowerCase().trim();
    renderBoard();
}

/** Priority chip filter — called by button onclick handlers in HTML. */
function filterByPriority(prio, btn) {
    activePriorityFilter = prio;
    document.querySelectorAll(".filter-chip").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    renderBoard();
}

window.filterCards      = filterCards;
window.filterByPriority = filterByPriority;

/* ================================================================== */
/*  Add Task Modal                                                      */
/* ================================================================== */

function _rpAddTaskPredSource() {
    if (rpGanttTasks && rpGanttTasks.length) return rpGanttTasks;
    return tasks;
}

function renderAddTaskPredPanel() {
    const wrap = document.getElementById("at-deps");
    const VG = window.VeritasGantt;
    if (!wrap || !VG) return;
    const src = _rpAddTaskPredSource();
    if (!src.length) {
        wrap.innerHTML = `<p style="color:var(--text-muted);font-size:0.78rem;margin:0;">No other tasks yet. Open this form after the schedule loads, or create tasks first.</p>`;
        return;
    }
    const rows = src.map((ot, k) => {
        const tid = VG.taskIdAtIndex(src, k);
        const safeName = escapeHtmlRp(ot.name || tid);
        return `<div class="gantt-dep-row">
      <label class="gantt-dep-label" for="at-dep-${k}">
        <input type="checkbox" class="gantt-dep-cb" id="at-dep-${k}" onchange="window.atToggleDepLag(${k})">
        <span class="gantt-dep-name">${safeName}</span>
        <span class="gantt-dep-id">${escapeHtmlRp(String(tid))}</span>
      </label>
      <label class="gantt-dep-lag-wrap">Lag (WD)
        <input type="number" class="rp-gantt-form-input gantt-dep-lag" id="at-dep-lag-${k}" min="0" step="1" value="0" disabled>
      </label>
    </div>`;
    }).join("");
    wrap.innerHTML = rows;
}

function atToggleDepLag(k) {
    const cb = document.getElementById(`at-dep-${k}`);
    const lag = document.getElementById(`at-dep-lag-${k}`);
    if (cb && lag) {
        lag.disabled = !cb.checked;
        if (!cb.checked) lag.value = "0";
    }
}

function atCollectAddTaskDeps() {
    const VG = window.VeritasGantt;
    if (!VG) return { deps: [], dep_lag_wd: [] };
    const src = _rpAddTaskPredSource();
    const pairs = [];
    for (let k = 0; k < src.length; k++) {
        const cb = document.getElementById(`at-dep-${k}`);
        if (cb && cb.checked) {
            const tid = VG.taskIdAtIndex(src, k);
            const lagEl = document.getElementById(`at-dep-lag-${k}`);
            const lag = Math.max(0, parseInt(lagEl && lagEl.value, 10) || 0);
            pairs.push({ idx: k, id: tid, lag });
        }
    }
    pairs.sort((a, b) => a.idx - b.idx);
    return {
        deps: pairs.map(p => p.id),
        dep_lag_wd: pairs.map(p => p.lag),
    };
}

function openAddTaskModal() {
    document.getElementById("at-name").value      = "";
    document.getElementById("at-desc").value      = "";
    document.getElementById("at-category").value  = "Foundation";
    document.getElementById("at-priority").value  = "med";
    document.getElementById("at-status").value    = "scheduled";
    document.getElementById("at-days").value      = "7";
    document.getElementById("at-pct").value       = "0";
    document.getElementById("at-start-date").value = "";
    document.getElementById("at-assignees").value = "";

    document.getElementById("at-name").classList.remove("field-error");
    document.getElementById("at-name-err").classList.remove("visible");

    renderAddTaskPredPanel();
    document.getElementById("addTaskModal").classList.add("open");
    setTimeout(() => document.getElementById("at-name").focus(), 60);
}

function closeAddTaskModal() {
    document.getElementById("addTaskModal").classList.remove("open");
}

function submitAddTask() {
    const nameEl = document.getElementById("at-name");
    const name   = nameEl.value.trim();

    if (!name) {
        nameEl.classList.add("field-error");
        document.getElementById("at-name-err").classList.add("visible");
        nameEl.focus();
        return;
    }
    nameEl.classList.remove("field-error");
    document.getElementById("at-name-err").classList.remove("visible");

    const rawAssignees = document.getElementById("at-assignees").value;
    const assignees = rawAssignees
        .split(",")
        .map(s => s.trim().toUpperCase())
        .filter(s => s.length > 0);
    if (assignees.length === 0) assignees.push("DN");

    const days = Math.max(0, parseInt(document.getElementById("at-days").value, 10) || 0);
    const pct  = Math.min(100, Math.max(0, parseInt(document.getElementById("at-pct").value, 10) || 0));
    const startDateRaw = (document.getElementById("at-start-date")?.value || "").trim();

    const newTask = {
        id:             "t" + Date.now(),
        name:           name,
        desc:           document.getElementById("at-desc").value.trim() || "No description provided.",
        status:         document.getElementById("at-status").value,
        schedule_pct:   pct,
        days_remaining: days,
        priority:       document.getElementById("at-priority").value,
        assignees:      assignees,
        category:       document.getElementById("at-category").value,
    };
    if (startDateRaw) newTask.start_date = startDateRaw.slice(0, 10);

    const { deps: atDeps, dep_lag_wd: atLags } = atCollectAddTaskDeps();
    if (atDeps.length) {
        newTask.deps = atDeps;
        newTask.dep_lag_wd = atLags;
    }

    tasks.push(newTask);
    saveTasks();
    renderBoard();
    closeAddTaskModal();
    showToast(`"${newTask.name}" added to ${newTask.status.replace("_", " ")}`, "success");

    fetch(rpApiUrl("/api/dashboard/tasks"), {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(newTask),
    })
        .then(async res => {
            if (!res.ok) {
                const j = await res.json().catch(() => ({}));
                showToast(j.message || `Could not persist task (HTTP ${res.status})`, "error");
                return;
            }
            await syncTasksFromAPI();
            await syncRpGanttFromAPI();
        })
        .catch(() => showToast("Could not persist task to server.", "error"));
}

// Close on backdrop click or Escape
document.addEventListener("click", e => {
    const overlay = document.getElementById("addTaskModal");
    if (overlay && e.target === overlay) closeAddTaskModal();
});
document.addEventListener("keydown", e => {
    if (e.key === "Escape") {
        closeAddTaskModal();
        rpCloseGanttTaskEditModal();
    }
});

window.openAddTaskModal  = openAddTaskModal;
window.closeAddTaskModal = closeAddTaskModal;
window.submitAddTask     = submitAddTask;
window.atToggleDepLag    = atToggleDepLag;

/* ================================================================== */
/*  Nav helpers — user menu, logout modal                              */
/* ================================================================== */

function toggleUserMenu() {
    document.getElementById("userDropdown").classList.toggle("open");
}

function showLogoutModal() {
    document.getElementById("userDropdown").classList.remove("open");
    document.getElementById("logoutOverlay").classList.add("open");
}

function hideLogoutModal() {
    document.getElementById("logoutOverlay").classList.remove("open");
}

function confirmLogout() {
    window.location.href = "/login?signedout=1";
}

// Expose nav helpers called from HTML onclick attributes
window.toggleUserMenu  = toggleUserMenu;
window.showLogoutModal = showLogoutModal;
window.hideLogoutModal = hideLogoutModal;
window.confirmLogout   = confirmLogout;

// Close user dropdown when clicking outside
document.addEventListener("click", e => {
    const wrap = document.querySelector(".user-menu-wrap");
    if (wrap && !wrap.contains(e.target)) {
        document.getElementById("userDropdown")?.classList.remove("open");
    }
});

/* ================================================================== */
/*  Gantt chart (wizard parity — editable, shared API + CPM)             */
/* ================================================================== */

function escapeHtmlRp(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
}

let rpGanttTasks = [];
let rpGanttDetails = { start_date: null, end_date: null };
let rpGanttEditIndex = -1;
/** @type {null | { index: number, track: Element, bar: Element, pointerId: number, startClientX: number, initialLeftPct: number, widthPct: number, moved: boolean }} */
let rpGanttDrag = null;

function rpGetSpan() {
    return window.VeritasGantt.getSpanFromDates(rpGanttDetails.start_date, rpGanttDetails.end_date);
}

/** Full project span for CPM when details lack dates (synthetic window from a task start). */
function rpSpanForCPM(fallbackStartISO) {
    const VG = window.VeritasGantt;
    if (!VG) return rpGetSpan();
    let span = rpGetSpan();
    if (span.startISO && span.endISO) return span;
    const anchor = VG.firstWorkingDayOnOrAfterISO(String(fallbackStartISO || "").slice(0, 10));
    let endISO = rpGanttDetails.end_date ? String(rpGanttDetails.end_date).slice(0, 10) : "";
    if (!endISO) {
        const a = VG.parseISODateUTC(anchor);
        if (a) {
            const e = new Date(a.getTime());
            e.setUTCDate(e.getUTCDate() + 365);
            endISO = e.toISOString().slice(0, 10);
        }
    }
    if (!endISO) endISO = anchor;
    if (anchor && endISO) span = VG.getSpanFromDates(anchor, endISO);
    return span;
}

function _rpEscapeHtmlGantt(s) {
    return escapeHtmlRp(s);
}

async function persistRpGanttToServer(opts = {}) {
    const quiet = opts.quiet === true;
    if (!rpActiveProjectId || !rpGanttTasks.length) return;
    try {
        const res = await fetch(`/api/new-project/active/${encodeURIComponent(rpActiveProjectId)}/gantt`, {
            method:  "PUT",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({ tasks: rpGanttTasks }),
        });
        if (!res.ok) throw new Error("save failed");
        await syncTasksFromAPI();
        if (!quiet) showToast("Schedule saved to project", "success");
    } catch {
        showToast("Could not save schedule to server.", "error");
    }
}

const RP_GANTT_HEADER_COLS = 12;
const RP_GANTT_FALLBACK_HEADERS = [
    "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun",
    "Week 2", "Week 3", "Week 4", "Week 5", "Week 6",
];

/** Header row HTML: calendar dates per column from project start→end (same 12 segments as bar %). */
function buildRpGanttHeaderRowHtml() {
    const VG = window.VeritasGantt;
    const span = rpGetSpan();
    if (!VG || !span.startISO || !span.endISO || span.totalDays < 1) {
        return RP_GANTT_FALLBACK_HEADERS.map(d =>
            `<div class="gantt-day-label gantt-day-label--simple">${escapeHtmlRp(d)}</div>`
        ).join("");
    }
    const projStart = VG.parseISODateUTC(String(span.startISO).slice(0, 10));
    if (!projStart) {
        return RP_GANTT_FALLBACK_HEADERS.map(d =>
            `<div class="gantt-day-label gantt-day-label--simple">${escapeHtmlRp(d)}</div>`
        ).join("");
    }
    const total = Math.max(1, span.totalDays);
    const dowNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    const cells = [];
    for (let c = 0; c < RP_GANTT_HEADER_COLS; c++) {
        const offsetDays = Math.floor((c * total) / RP_GANTT_HEADER_COLS);
        const d = new Date(projStart.getTime());
        d.setUTCDate(d.getUTCDate() + offsetDays);
        const mo = d.getUTCMonth() + 1;
        const day = d.getUTCDate();
        const y = d.getUTCFullYear();
        const dow = dowNames[d.getUTCDay()];
        const dateShort = `${mo}/${day}`;
        const title = `${dow}, ${y}-${String(mo).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
        const tEsc = String(title).replace(/&/g, "&amp;").replace(/"/g, "&quot;");
        cells.push(
            `<div class="gantt-day-label" title="${tEsc}">` +
            `<span class="gantt-hd-dow">${escapeHtmlRp(dow)}</span>` +
            `<span class="gantt-hd-date">${escapeHtmlRp(dateShort)}</span></div>`
        );
    }
    return cells.join("");
}

function renderRpGantt() {
    const el = document.getElementById("rpGanttContainer");
    if (!el) return;
    const ganttTasks = rpGanttTasks;
    if (!ganttTasks.length) {
        el.innerHTML = `<p style="color:var(--text-muted);font-size:0.85rem;">No scheduled tasks yet for this project.</p>`;
        return;
    }
    el.innerHTML = `
        <div class="gantt-wrap">
            <div class="gantt-header-row">
                ${buildRpGanttHeaderRowHtml()}
            </div>
            ${ganttTasks.map((t, i) => {
                const left = Number(t.start_offset_pct) || 0;
                const w = Math.max(Number(t.width_pct) || 3, 0.3);
                const dur = t.duration != null ? t.duration : (t.duration_wd != null ? t.duration_wd : "");
                const nm = escapeHtmlRp(t.name || "Task");
                const idJs = JSON.stringify(String(t.id));
                const barCol = i % 2 === 0 ? "var(--accent-blue)" : "var(--accent-teal)";
                return `<div class="gantt-task-row">
                    <div class="gantt-task-label-cell">
                        <span class="gantt-task-label" title="${nm}">${nm}</span>
                        <button type="button" class="gantt-row-delete" title="Delete task" aria-label="Delete"
                            onclick="event.stopPropagation();window.deleteTaskById(${idJs})">×</button>
                    </div>
                    <div class="gantt-track">
                        <div class="gantt-bar gantt-bar--draggable" role="button" tabindex="0"
                            title="Drag to reschedule, or click to edit"
                            aria-label="Drag to reschedule or click to edit task"
                            style="left:${left}%;width:${Math.max(w, 3)}%;background:${barCol};"
                            onpointerdown="window.rpGanttBarPointerDown(event, ${i})"
                            onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();window.rpOpenGanttTaskEditor(${i});}">
                            ${dur}d
                        </div>
                    </div>
                </div>`;
            }).join("")}
        </div>
        <div class="rp-gantt-footer">${ganttTasks.length} task(s) · Mon–Fri working days, lag &amp; crew pools — same editor as the New Project wizard</div>
        <div style="margin-top:1rem;display:flex;gap:0.75rem;flex-wrap:wrap;">
            <button type="button" class="btn-rp-secondary" onclick="window.rpReloadGantt()">Reload from server</button>
        </div>
    `;
}

function rpGanttBarPointerDown(e, taskIndex) {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    const bar = e.currentTarget;
    const track = bar.closest(".gantt-track");
    const VG = window.VeritasGantt;
    if (!track || !VG || !rpGanttTasks[taskIndex]) return;
    e.stopPropagation();
    e.preventDefault();
    const t = rpGanttTasks[taskIndex];
    const initialLeftPct = Number(t.start_offset_pct) || 0;
    const widthPct = Math.max(Number(t.width_pct) || 3, 0.3);
    rpGanttDrag = {
        index:        taskIndex,
        track,
        bar,
        pointerId:    e.pointerId,
        startClientX: e.clientX,
        initialLeftPct,
        widthPct,
        moved:        false,
    };
    bar.classList.add("gantt-bar-dragging");
    bar.style.transition = "none";
    try {
        bar.setPointerCapture(e.pointerId);
    } catch (_) { /* ignore */ }
    bar.addEventListener("pointermove", rpGanttBarPointerMove);
    bar.addEventListener("pointerup", rpGanttBarPointerUp);
    bar.addEventListener("pointercancel", rpGanttBarPointerUp);
}

function rpGanttBarPointerMove(e) {
    const st = rpGanttDrag;
    if (!st || e.pointerId !== st.pointerId) return;
    const tw = st.track.getBoundingClientRect().width;
    if (tw < 1) return;
    const dx = e.clientX - st.startClientX;
    if (Math.abs(dx) > 3) st.moved = true;
    const deltaPct = (dx / tw) * 100;
    let newLeft = st.initialLeftPct + deltaPct;
    newLeft = Math.max(0, Math.min(100 - st.widthPct, newLeft));
    st.bar.style.left = `${newLeft}%`;
}

function rpGanttBarPointerUp(e) {
    const st = rpGanttDrag;
    if (!st || e.pointerId !== st.pointerId) return;
    rpGanttDrag = null;
    const bar = st.bar;
    bar.style.transition = "";
    bar.classList.remove("gantt-bar-dragging");
    bar.removeEventListener("pointermove", rpGanttBarPointerMove);
    bar.removeEventListener("pointerup", rpGanttBarPointerUp);
    bar.removeEventListener("pointercancel", rpGanttBarPointerUp);
    try {
        bar.releasePointerCapture(e.pointerId);
    } catch (_) { /* ignore */ }

    if (!st.moved) {
        rpOpenGanttTaskEditor(st.index);
        return;
    }
    const tw = st.track.getBoundingClientRect().width;
    if (tw < 1) {
        renderRpGantt();
        return;
    }
    const m = bar.style.left.match(/([\d.]+)%/);
    let leftPct = m ? parseFloat(m[1]) : st.initialLeftPct;
    leftPct = Math.max(0, Math.min(100 - st.widthPct, leftPct));
    void rpCommitGanttBarDrag(st.index, leftPct);
}

function rpSyncBoardTasksFromGantt() {
    const byId = new Map(rpGanttTasks.map(t => [String(t.id), t]));
    for (const task of tasks) {
        const g = byId.get(String(task.id));
        if (!g) continue;
        if (g.start_date) task.start_date = String(g.start_date).slice(0, 10);
        const dw = g.duration_wd != null ? g.duration_wd : g.duration;
        if (dw != null && Number.isFinite(Number(dw)) && Number(dw) >= 1) {
            task.duration_wd = Math.round(Number(dw));
        }
        if (Object.prototype.hasOwnProperty.call(g, "cost")) {
            if (g.cost == null || g.cost === "" || !Number.isFinite(Number(g.cost))) {
                delete task.cost;
            } else {
                task.cost = Math.round(Number(g.cost) * 100) / 100;
            }
        }
        if (Object.prototype.hasOwnProperty.call(g, "inspection_required")) {
            const ir = g.inspection_required;
            const truthy = ir === true || ir === 1 || String(ir).toLowerCase() === "true";
            if (!truthy) {
                delete task.inspection_required;
                delete task.inspection_date;
            } else {
                task.inspection_required = true;
                if (g.inspection_date) task.inspection_date = String(g.inspection_date).slice(0, 10);
                else delete task.inspection_date;
            }
        }
    }
}

async function rpCommitGanttBarDrag(taskIndex, leftPct) {
    const VG = window.VeritasGantt;
    if (!VG || taskIndex < 0 || taskIndex >= rpGanttTasks.length) return;
    const span0 = rpGetSpan();
    if (!span0.startISO || span0.totalDays < 1) {
        showToast("Project dates are required to reschedule from the chart.", "error");
        renderRpGantt();
        return;
    }
    const proj = VG.parseISODateUTC(String(span0.startISO).slice(0, 10));
    if (!proj) {
        renderRpGantt();
        return;
    }
    const total = Math.max(1, span0.totalDays);
    const offsetDays = Math.round((leftPct / 100) * total);
    const d = new Date(proj.getTime());
    d.setUTCDate(d.getUTCDate() + offsetDays);
    let minISO = d.toISOString().slice(0, 10);
    minISO = VG.firstWorkingDayOnOrAfterISO(minISO);
    if (span0.startISO && VG.daysBetweenUTC(VG.parseISODateUTC(span0.startISO), VG.parseISODateUTC(minISO)) < 0) {
        minISO = String(span0.startISO).slice(0, 10);
    }

    const span = rpSpanForCPM(minISO);
    if (!span.startISO) {
        renderRpGantt();
        return;
    }

    const cpm = VG.recalculateGanttCPM(rpGanttTasks, span, {
        editedIndex:       taskIndex,
        editedMinStartISO: minISO,
    });
    renderRpGantt();
    if (cpm.overriddenStart) {
        showToast("Start adjusted for dependencies or crew leveling.", "info");
    }
    if (cpm.anyPastProjectEnd) {
        showToast("Some tasks extend past the project end date.", "info");
    }
    if (rpActiveProjectId) {
        try {
            const res = await fetch(`/api/new-project/active/${encodeURIComponent(rpActiveProjectId)}/gantt`, {
                method:  "PUT",
                headers: { "Content-Type": "application/json" },
                body:    JSON.stringify({ tasks: rpGanttTasks }),
            });
            if (!res.ok) throw new Error("save failed");
            await syncTasksFromAPI();
        } catch {
            showToast("Could not save schedule to server.", "error");
            await syncRpGanttFromAPI();
            return;
        }
    } else {
        rpSyncBoardTasksFromGantt();
        saveTasks();
        renderBoard();
    }
    showToast("Task rescheduled", "success");
}

function rpRefreshGanttEditEndPreview() {
    const VG = window.VeritasGantt;
    const start = document.getElementById("rpGanttEditStart")?.value;
    const durEl = document.getElementById("rpGanttEditDuration");
    const out = document.getElementById("rpGanttEditEndDisplay");
    if (!start || !durEl || !out || !VG) return;
    const dur = Math.max(1, parseInt(durEl.value, 10) || 1);
    const s0 = VG.firstWorkingDayOnOrAfterISO(start);
    out.textContent = VG.taskLastDayFromStartAndWdDuration(s0, dur);
}

function rpRenderGanttEditDepsPanel(taskIndex) {
    const wrap = document.getElementById("rpGanttEditDeps");
    const VG = window.VeritasGantt;
    if (!wrap || !VG) return;
    const gTasks = rpGanttTasks;
    const t = gTasks[taskIndex];
    if (!t) {
        wrap.innerHTML = "";
        return;
    }
    const depList = Array.isArray(t.deps) ? t.deps.map(String) : [];
    const lagList = Array.isArray(t.dep_lag_wd) ? t.dep_lag_wd : [];
    const lagByPred = new Map();
    depList.forEach((id, idx) => {
        lagByPred.set(id, Math.max(0, parseInt(lagList[idx], 10) || 0));
    });

    const rows = gTasks.map((ot, k) => {
        if (k === taskIndex) return "";
        const tid = VG.taskIdAtIndex(gTasks, k);
        const checked = depList.includes(String(tid)) ? "checked" : "";
        const lag = lagByPred.has(String(tid)) ? lagByPred.get(String(tid)) : 0;
        const safeName = _rpEscapeHtmlGantt(ot.name || tid);
        return `<div class="gantt-dep-row">
      <label class="gantt-dep-label" for="rp-gantt-dep-${k}">
        <input type="checkbox" class="gantt-dep-cb" id="rp-gantt-dep-${k}" ${checked} onchange="window.rpToggleGanttDepLag(${k})">
        <span class="gantt-dep-name">${safeName}</span>
        <span class="gantt-dep-id">${tid}</span>
      </label>
      <label class="gantt-dep-lag-wrap">Lag (WD)
        <input type="number" class="rp-gantt-form-input gantt-dep-lag" id="rp-gantt-dep-lag-${k}" min="0" step="1" value="${lag}" ${checked ? "" : "disabled"}>
      </label>
    </div>`;
    }).join("");

    wrap.innerHTML = rows.trim()
        ? rows
        : `<div style="color:var(--text-muted);font-size:0.8rem;">No other tasks.</div>`;
}

function rpToggleGanttDepLag(k) {
    const cb = document.getElementById(`rp-gantt-dep-${k}`);
    const lag = document.getElementById(`rp-gantt-dep-lag-${k}`);
    if (cb && lag) {
        lag.disabled = !cb.checked;
        if (!cb.checked) lag.value = "0";
    }
}

function rpCollectGanttEditDeps(taskIndex) {
    const VG = window.VeritasGantt;
    const gTasks = rpGanttTasks;
    const pairs = [];
    for (let k = 0; k < gTasks.length; k++) {
        if (k === taskIndex) continue;
        const cb = document.getElementById(`rp-gantt-dep-${k}`);
        if (cb && cb.checked) {
            const tid = VG.taskIdAtIndex(gTasks, k);
            const lagEl = document.getElementById(`rp-gantt-dep-lag-${k}`);
            const lag = Math.max(0, parseInt(lagEl && lagEl.value, 10) || 0);
            pairs.push({ idx: k, id: tid, lag });
        }
    }
    pairs.sort((a, b) => a.idx - b.idx);
    return {
        deps: pairs.map(p => p.id),
        dep_lag_wd: pairs.map(p => p.lag),
    };
}

function rpOpenGanttTaskEditor(taskIndex) {
    const VG = window.VeritasGantt;
    if (!VG) return;
    const t = rpGanttTasks[taskIndex];
    if (!t) return;
    rpGanttEditIndex = taskIndex;
    document.getElementById("rpGanttEditName").value = t.name || "";
    document.getElementById("rpGanttEditStart").value = t.start_date || "";
    document.getElementById("rpGanttEditDuration").value = String(t.duration ?? 1);
    const rpCostEl = document.getElementById("rpGanttEditCost");
    if (rpCostEl) {
        rpCostEl.value = t.cost != null && t.cost !== "" && Number.isFinite(Number(t.cost)) ? String(Number(t.cost)) : "";
    }
    const rpInspCb = document.getElementById("rpGanttEditInspectionNeeded");
    const rpInspDate = document.getElementById("rpGanttEditInspectionDate");
    if (rpInspCb) {
        const ir = t.inspection_required;
        rpInspCb.checked = ir === true || ir === 1 || String(ir).toLowerCase() === "true";
    }
    if (rpInspDate) {
        const id = t.inspection_date;
        rpInspDate.value = id ? String(id).slice(0, 10) : "";
    }
    syncRpGanttInspectionFields();
    rpRefreshGanttEditEndPreview();
    rpRenderGanttEditDepsPanel(taskIndex);
    document.getElementById("rpGanttTaskEditModal").classList.add("open");
}

function rpCloseGanttTaskEditModal() {
    const m = document.getElementById("rpGanttTaskEditModal");
    if (m) m.classList.remove("open");
    rpGanttEditIndex = -1;
}

async function rpSaveGanttTaskEdit() {
    const VG = window.VeritasGantt;
    if (!VG) return;
    const i = rpGanttEditIndex;
    if (i < 0 || i >= rpGanttTasks.length) {
        rpCloseGanttTaskEditModal();
        return;
    }
    const name = document.getElementById("rpGanttEditName").value.trim();
    const start = document.getElementById("rpGanttEditStart").value;
    const dur = parseInt(document.getElementById("rpGanttEditDuration").value, 10);
    if (!name) {
        showToast("Task name is required.", "error");
        return;
    }
    if (!start) {
        showToast("Start date is required.", "error");
        return;
    }
    if (!Number.isFinite(dur) || dur < 1) {
        showToast("Duration must be at least 1 day.", "error");
        return;
    }
    const costRaw = (document.getElementById("rpGanttEditCost")?.value || "").trim();
    const parsedCost = costRaw === "" ? null : Number(costRaw);
    if (costRaw !== "" && (!Number.isFinite(parsedCost) || parsedCost < 0)) {
        showToast("Task cost must be a non-negative number.", "error");
        return;
    }
    let span = rpGetSpan();
    if (span.startISO && VG.daysBetweenUTC(VG.parseISODateUTC(span.startISO), VG.parseISODateUTC(start)) < 0) {
        showToast("Start date cannot be before the project start.", "error");
        return;
    }
    const task = rpGanttTasks[i];
    const prevDeps = Array.isArray(task.deps) ? [...task.deps] : [];
    const prevLags = Array.isArray(task.dep_lag_wd) ? [...task.dep_lag_wd] : [];
    const built = rpCollectGanttEditDeps(i);
    task.deps = built.deps;
    task.dep_lag_wd = built.dep_lag_wd;
    if (VG.ganttGraphHasCycle(rpGanttTasks)) {
        task.deps = prevDeps;
        task.dep_lag_wd = prevLags;
        rpRenderGanttEditDepsPanel(i);
        showToast("Those predecessors create a cycle. Change the links and try again.", "error");
        return;
    }
    task.name = name;
    task.duration = dur;
    task.duration_wd = dur;
    if (parsedCost == null) {
        delete task.cost;
    } else {
        task.cost = Math.round(parsedCost * 100) / 100;
    }

    const rpInspCb2 = document.getElementById("rpGanttEditInspectionNeeded");
    const rpInspDateEl = document.getElementById("rpGanttEditInspectionDate");
    const rpInspectionNeeded = !!(rpInspCb2 && rpInspCb2.checked);
    if (!rpInspectionNeeded) {
        delete task.inspection_required;
        delete task.inspection_date;
    } else {
        task.inspection_required = true;
        const rid = (rpInspDateEl && rpInspDateEl.value ? String(rpInspDateEl.value) : "").trim().slice(0, 10);
        if (rid) task.inspection_date = rid;
        else delete task.inspection_date;
    }

    span = rpSpanForCPM(start);

    const cpm = VG.recalculateGanttCPM(rpGanttTasks, span, {
        editedIndex: i,
        editedMinStartISO: start,
    });
    if (cpm.overriddenStart) {
        showToast("Start date moved later to satisfy dependencies.", "info");
    }
    if (cpm.anyPastProjectEnd) {
        showToast("Some tasks now end after the project end date — adjust if needed.", "info");
    }
    renderRpGantt();
    rpCloseGanttTaskEditModal();
    if (rpActiveProjectId) {
        await persistRpGanttToServer({ quiet: true });
    } else {
        rpSyncBoardTasksFromGantt();
        saveTasks();
        renderBoard();
    }
    showToast("Schedule updated (working-day CPM + leveling)", "success");
}

async function rpReloadGantt() {
    await syncRpGanttFromAPI();
    showToast("Schedule reloaded", "info");
}

function rpDeleteGanttTask() {
    const i = rpGanttEditIndex;
    if (i < 0 || !rpGanttTasks[i]) return;
    deleteTaskById(rpGanttTasks[i].id);
}

async function syncRpGanttFromAPI() {
    const el = document.getElementById("rpGanttContainer");
    if (!el) return;
    if (!rpActiveProjectId) {
        rpGanttTasks = [];
        el.innerHTML = `<p class="rp-gantt-placeholder" style="color:var(--text-muted);font-size:0.85rem;">Select a project to load the schedule chart.</p>`;
        return;
    }
    el.innerHTML = `<div class="rp-gantt-loading" style="color:var(--text-muted);font-size:0.85rem;padding:0.5rem 0;">Loading schedule…</div>`;
    try {
        const res = await fetch(`/api/new-project/active/${encodeURIComponent(rpActiveProjectId)}/gantt`);
        const json = await res.json().catch(() => ({}));
        if (!res.ok) {
            const msg = escapeHtmlRp(json.message || "Could not load Gantt for this project.");
            el.innerHTML = `<p class="rp-gantt-error" style="color:var(--accent-orange);font-size:0.85rem;">${msg}</p>`;
            rpGanttTasks = [];
            return;
        }
        rpGanttTasks = Array.isArray(json.data) ? json.data.map(t => ({ ...t })) : [];
        try {
            const pr = await fetch(`/api/new-project/active/${encodeURIComponent(rpActiveProjectId)}`);
            const pj = await pr.json().catch(() => ({}));
            const det = pj.data && pj.data.details;
            if (det) {
                rpGanttDetails = { start_date: det.start_date || null, end_date: det.end_date || null };
            }
        } catch {
            rpGanttDetails = { start_date: null, end_date: null };
        }
        renderRpGantt();
    } catch {
        el.innerHTML = `<p class="rp-gantt-error" style="color:var(--accent-orange);font-size:0.85rem;">Could not load schedule chart.</p>`;
        rpGanttTasks = [];
    }
}

window.rpOpenGanttTaskEditor = rpOpenGanttTaskEditor;
window.rpGanttBarPointerDown = rpGanttBarPointerDown;
window.rpCloseGanttTaskEditModal = rpCloseGanttTaskEditModal;
window.rpSaveGanttTaskEdit     = rpSaveGanttTaskEdit;
window.rpReloadGantt           = rpReloadGantt;
window.rpRefreshGanttEditEndPreview = rpRefreshGanttEditEndPreview;
window.rpToggleGanttDepLag     = rpToggleGanttDepLag;
window.rpDeleteGanttTask       = rpDeleteGanttTask;
window.syncRpKanbanInspectionFields = syncRpKanbanInspectionFields;
window.syncRpGanttInspectionFields = syncRpGanttInspectionFields;

/* ================================================================== */
/*  Init                                                                */
/* ================================================================== */

/**
 * Fetch tasks from the API; fall back to TASK_SEED if unavailable.
 * Then render the Kanban board.
 */
/**
 * Fetch tasks from the API and re-render the board if live data is returned.
 *
 * IMPORTANT: renderBoard() is called immediately with seed data BEFORE this
 * async function is awaited — so the board is always populated on first paint.
 * This function only updates the board if the server responds with real tasks.
 */
async function syncTasksFromAPI() {
    const beforeSync = tasks.slice();
    let hadSavedBoard = false;
    try {
        const raw = localStorage.getItem(getRpTasksStorageKey());
        if (raw) {
            const parsed = JSON.parse(raw);
            hadSavedBoard = Array.isArray(parsed) && parsed.length > 0;
        }
    } catch {
        hadSavedBoard = false;
    }

    try {
        const path = rpActiveProjectId ? "/api/dashboard/tasks?full=1" : "/api/dashboard/tasks";
        const res  = await fetch(rpApiUrl(path));
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json();
        const list = Array.isArray(json.data) ? json.data : [];

        if (rpActiveProjectId) {
            const localById = new Map(beforeSync.map(t => [String(t.id), t]));
            const newTasks = list.map((t, i) => {
                const id = t.id ?? `api-${i}`;
                const sd = t.start_date ? String(t.start_date).slice(0, 10) : "";
                let duration_wd;
                if (t.duration_wd != null && t.duration_wd !== "") {
                    const n = Number(t.duration_wd);
                    if (Number.isFinite(n) && n >= 1) duration_wd = Math.round(n);
                }
                let taskCost;
                if (t.cost != null && t.cost !== "") {
                    const cn = Number(t.cost);
                    if (Number.isFinite(cn) && cn >= 0) taskCost = Math.round(cn * 100) / 100;
                }
                let inspExtra = {};
                const ir = t.inspection_required;
                if (ir === true || ir === 1 || String(ir).toLowerCase() === "true") {
                    inspExtra.inspection_required = true;
                    const idd = t.inspection_date ? String(t.inspection_date).slice(0, 10) : "";
                    if (idd.length === 10) inspExtra.inspection_date = idd;
                }
                const normalized = normalizeTaskForKanban({
                    id:             id,
                    name:           t.name,
                    desc:           t.description ?? t.desc ?? "No description provided.",
                    status:         t.status ?? "scheduled",
                    schedule_pct:   t.schedule_pct ?? 0,
                    days_remaining: t.days_remaining ?? 0,
                    priority:       t.priority ?? "med",
                    assignees:      Array.isArray(t.assignees) && t.assignees.length ? t.assignees : ["DN"],
                    category:       t.category ?? "General",
                    ...(sd ? { start_date: sd } : {}),
                    ...(duration_wd != null ? { duration_wd: duration_wd } : {}),
                    ...(taskCost != null ? { cost: taskCost } : {}),
                    ...inspExtra,
                });
                const loc = localById.get(String(id));
                if (loc && KANBAN_COLUMN_STATUSES.has(loc.status)) {
                    const merged = { ...normalized, status: loc.status };
                    if (loc.status === "completed") merged.schedule_pct = 100;
                    return normalizeTaskForKanban(merged);
                }
                return normalized;
            });
            // Server full list is authoritative: do not re-merge local rows that were
            // removed on the server (e.g. deleted from Gantt/Kanban), which used to
            // revive them as "pendingLocal".
            tasks = newTasks;
            saveTasks();
            renderBoard();
            if (list.length) showToast("Tasks loaded from project schedule", "success");
            return;
        }

        if (list.length) {
            const localById = new Map(beforeSync.map(t => [String(t.id), t]));

            const newTasks = list.map((t, i) => {
                const id = t.id ?? `api-${i}`;
                const normalized = normalizeTaskForKanban({
                    id:             id,
                    name:           t.name,
                    desc:           t.description ?? t.desc ?? "No description provided.",
                    status:         t.status ?? "scheduled",
                    schedule_pct:   t.schedule_pct ?? 0,
                    days_remaining: t.days_remaining ?? 0,
                    priority:       t.priority ?? "med",
                    assignees:      Array.isArray(t.assignees) && t.assignees.length ? t.assignees : ["DN"],
                    category:       t.category ?? "General",
                });
                /* Drag-and-drop column is stored locally; API dates do not match Kanban columns */
                const loc = localById.get(String(id));
                if (loc && KANBAN_COLUMN_STATUSES.has(loc.status)) {
                    return normalizeTaskForKanban({ ...normalized, status: loc.status });
                }
                return normalized;
            });

            const apiIds = new Set(newTasks.map(t => String(t.id)));
            let extras;
            if (hadSavedBoard) {
                extras = beforeSync.filter(t => !apiIds.has(String(t.id)));
            } else {
                extras = beforeSync.filter(
                    t =>
                        (t.status === "review" || t.status === "completed") &&
                        !apiIds.has(String(t.id))
                );
            }

            tasks = [...newTasks, ...normalizeTaskList(extras)];
            saveTasks();
            renderBoard();
            showToast("Tasks loaded from server", "success");
        }
    } catch {
        // API unavailable → keep current board
    }
}

/**
 * Main entry point: Kanban first paint, then optional API upgrade.
 *
 * WHY readyState CHECK (at bottom of file):
 *   Scripts at the bottom of <body> execute after the DOM is parsed, meaning
 *   DOMContentLoaded has already fired. addEventListener("DOMContentLoaded")
 *   at that point registers a callback that is never invoked. The readyState
 *   guard handles both cases: early load (wait for event) and late load (call
 *   directly).
 */
async function init() {
    await loadResourcePlanProjectSwitcher();
    updateResourcePlanNavLinks();
    tasks = loadTasks();
    renderBoard();
    await syncTasksFromAPI();
    await syncRpGanttFromAPI();
}

function runInit() {
    init().catch(err => console.error("[Resource Plan] Init error:", err));
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", runInit);
} else {
    runInit();
}
})(); // end resource_plan IIFE