/**
 * static/js/new_project.js
 * ========================
 * UC-09 — New Project Initialization Wizard
 * 10-step state machine that drives the full wizard UI.
 *
 * Architecture:
 *   - STATE object holds all wizard data in memory
 *   - Each step has: load(), validate(), save() functions
 *   - Navigation is handled by nextStep() / prevStep()
 *   - Every save() posts to the backend API before advancing
 */

"use strict";

// ─────────────────────────────────────────────────────────
// GLOBAL STATE
// ─────────────────────────────────────────────────────────
const STATE = {
    draftId:       null,
    /** When set, wizard updates a published project via /api/new-project/active/... */
    editProjectId: null,
    currentStep:   1,
    completedSteps: new Set(),
    buildingTypes: [],
    selectedBuildingType: null,
    zones:         [],
    teamMembers:   [],
    ganttTasks:    [],
    safetyRules:   [],
    vrMatrix:      [],
    vrDeadline:    null,
    uploadedDocs:  [],
    publishedProjectId: null,
    userDirectory: [],
    projectDetails: null,
};

/** Index of task being edited in Step 5 modal; -1 when closed */
let ganttEditTaskIndex = -1;

/** Step 5 Gantt bar drag (pointer capture) */
let wizardGanttDrag = null;

// Step metadata
const STEPS = [
    { num: 1, title: "Select Building Type",           desc: "Choose the category and type of building for this project.",                    skippable: false },
    { num: 2, title: "Enter Project Details",           desc: "Provide the project name, location, dates, and budget.",                       skippable: false },
    { num: 3, title: "Define Site Zones",               desc: "Review and customise AI-suggested site monitoring zones.",                     skippable: true  },
    { num: 4, title: "Assign Team Members",             desc: "Add students and staff and assign their project roles.",                       skippable: false },
    { num: 5, title: "Review Resource Plan",            desc: "Review the AI-generated Gantt chart and accept or edit the schedule.",         skippable: false },
    { num: 6, title: "Review Safety Protocols",         desc: "Confirm AI-loaded safety rules and their zone mappings.",                      skippable: false },
    { num: 7, title: "VR Training Assignments",         desc: "Review and confirm mandatory VR training modules for each team member.",       skippable: false },
    { num: 8, title: "Upload Project Documents",        desc: "Upload blueprints, permits, and supporting documents (optional).",             skippable: true  },
    { num: 9, title: "Review & Publish",                desc: "Review the complete project checklist before publishing.",                     skippable: false },
    { num: 10, title: "Project Published",              desc: "",                                                                             skippable: false },
];

// Zone colour palette
const ZONE_COLOURS = ["#4F8EF7","#3DD68C","#F59E0B","#F06060","#A78BFA","#EC4899","#06B6D4","#84CC16"];

// ─────────────────────────────────────────────────────────
// INIT
// ─────────────────────────────────────────────────────────
function wizardApiBase() {
    if (STATE.editProjectId) return `/api/new-project/active/${STATE.editProjectId}`;
    if (STATE.draftId) return `/api/new-project/draft/${STATE.draftId}`;
    throw new Error("No wizard context");
}

function isEditMode() {
    return !!STATE.editProjectId;
}

document.addEventListener("DOMContentLoaded", () => {
    loadProjectList();
    const params = new URLSearchParams(window.location.search);
    const editId = (params.get("edit") || "").trim();
    if (editId && editId.startsWith("PRJ-")) {
        startEditWizard(editId);
    }
});

async function loadProjectList() {
    try {
        const res  = await apiFetch("/api/new-project/projects");
        const grid = document.getElementById("projectGrid");
        const empty= document.getElementById("projectEmpty");
        const count= document.getElementById("projectCount");

        if (!res.data || res.data.length === 0) {
            empty.style.display = "block";
            grid.style.display  = "none";
            count.textContent   = "0 projects";
            return;
        }

        count.textContent = `${res.data.length} project(s)`;
        grid.innerHTML = res.data.map(p => `
            <div class="project-card" onclick="openProject('${p.id}')">
                <div class="project-card-header">
                    <div>
                        <div class="project-card-name">${p.name}</div>
                        <div class="project-card-building">${p.building || "Building type not set"}</div>
                    </div>
                    <span class="status-badge badge-${p.status}">${p.status}</span>
                </div>
                <div style="font-size:0.75rem;color:var(--muted);font-family:var(--mono)">${p.id}</div>
                <div class="project-card-footer">
                    <button class="btn-delete-card" onclick="event.stopPropagation(); confirmDeleteProject('${p.id}', '${p.name}')">🗑 Delete</button>
                </div>
            </div>
        `).join("");
        empty.style.display = "none";
        grid.style.display  = "grid";
    } catch {
        // First run — no projects yet
        document.getElementById("projectEmpty").style.display = "block";
        document.getElementById("projectGrid").style.display  = "none";
        document.getElementById("projectCount").textContent   = "0 projects";
    }
}

async function openProject(id) {
    try {
        const res = await apiFetch("/api/new-project/draft/" + id);
        const d   = res.data || res;

        // Active/published — open edit wizard
        if (d.status && d.status !== "draft") {
            startEditWizard(id);
            return;
        }

        // Draft — restore STATE
        STATE.draftId              = id;
        STATE.selectedBuildingType = JSON.parse(localStorage.getItem("draft_buildingType_" + id) || "null") || d.building_type || null;
        STATE.zones                = d.zones         || [];
        STATE.teamMembers          = JSON.parse(localStorage.getItem("draft_team_" + id) || "null") || d.members || d.team || [];
        STATE.ganttTasks           = JSON.parse(localStorage.getItem("draft_gantt_"   + id) || "null") || d.tasks || d.gantt_tasks || [];
        const _safetyLS            = JSON.parse(localStorage.getItem("draft_safety_"  + id) || "null");
        STATE.safetyRules          = (_safetyLS?.rules) || d.rules || d.safety_rules || [];
        STATE.vrMatrix             = d.matrix        || d.vr_matrix   || [];
        STATE.vrDeadline           = localStorage.getItem("draft_vrDeadline_" + id) || d.compliance_deadline || null;
        STATE.uploadedDocs         = d.documents     || [];

        // Restore Step 2 form fields
        const set = (elId, val) => { const el = document.getElementById(elId); if (el && val != null) el.value = val; };
        set("inp-project_name", d.project_name);
        set("inp-client_org",   d.client_org);
        set("inp-site_address", d.site_address);
        set("inp-currency",     d.currency);
        set("inp-start_date",   d.start_date);
        set("inp-end_date",     d.end_date);
        set("inp-budget",       d.budget);
        set("inp-description",  d.description);

        // Restore projectDetails into STATE so Step 9 checklist can read it
        const _detailsLS = JSON.parse(localStorage.getItem("draft_details_" + id) || "null");
        STATE.projectDetails = _detailsLS || {
            project_name: d.project_name,
            client_org:   d.client_org,
            site_address: d.site_address,
            currency:     d.currency,
            start_date:   d.start_date,
            end_date:     d.end_date,
            budget:       d.budget,
            description:  d.description,
        };

        // Restore completed steps — use localStorage for lastStep (reliable, no API dependency)
        const lastStep = parseInt(localStorage.getItem("draft_lastStep_" + id) || d.last_step || "1", 10);
        STATE.completedSteps = new Set();
        for (let i = 1; i < lastStep; i++) STATE.completedSteps.add(i);

        // Open wizard at last saved step
        document.getElementById("projectListView").style.display = "none";
        document.getElementById("wizardView").style.display      = "block";
        document.getElementById("draftIdTag").textContent        = id;
        STATE.editProjectId = null;
        applyWizardChrome();
        buildSidebar();
        goToStep(lastStep);

    } catch {
        window.location.href = "/dashboard?project=" + id;
    }
}

function applyWizardChrome() {
    const h1 = document.querySelector("#wizardView .page-header h1");
    const p  = document.querySelector("#wizardView .page-header p");
    const exitBtn = document.getElementById("btnSaveExit");
    const tag = document.getElementById("draftIdTag");
    const lbl = document.getElementById("wizardContextLabel");
    const navHome = document.getElementById("navHomeLink");
    if (isEditMode()) {
        if (h1) h1.textContent = "Edit Project";
        if (p)  p.textContent  = "Update your project using the same steps as new projects. Save changes on each step, then review on the last step.";
        if (lbl) lbl.textContent = "Project:";
        if (tag) tag.textContent = STATE.editProjectId || "—";
        if (exitBtn) {
            exitBtn.style.display = "none";
            exitBtn.textContent = "Save Draft & Exit";
        }
        if (navHome && STATE.editProjectId) {
            navHome.href = "/dashboard?project=" + encodeURIComponent(STATE.editProjectId);
        }
    } else {
        if (h1) h1.textContent = "New Project Wizard";
        if (p)  p.textContent  = "Follow the steps to initialise and publish your construction project.";
        if (lbl) lbl.textContent = "Draft:";
        if (tag) tag.textContent = STATE.draftId || "—";
        if (exitBtn) {
            exitBtn.style.removeProperty("display");
            exitBtn.textContent = "Save Draft & Exit";
        }
        if (navHome) navHome.href = "/dashboard";
    }
}

async function startEditWizard(projectId) {
    try {
        const res = await apiFetch(`/api/new-project/active/${encodeURIComponent(projectId)}`);
        const d   = res.data || {};

        STATE.editProjectId = projectId;
        STATE.draftId       = null;
        STATE.publishedProjectId = null;

        const b = d.building;
        if (b && b.building_type_id) {
            STATE.selectedBuildingType = {
                id: b.building_type_id,
                name: b.name,
                category: b.category || "",
                description: "",
                icon: "🏗️",
            };
        } else {
            STATE.selectedBuildingType = null;
        }

        STATE.zones       = Array.isArray(d.zones) ? [...d.zones] : [];
        STATE.teamMembers = (d.team && d.team.members) ? [...d.team.members] : [];
        STATE.ganttTasks  = Array.isArray(d.gantt_tasks) ? [...d.gantt_tasks] : [];
        STATE.safetyRules = Array.isArray(d.safety_rules) ? [...d.safety_rules] : [];
        STATE.vrMatrix    = Array.isArray(d.vr_matrix) ? [...d.vr_matrix] : [];
        STATE.vrDeadline  = d.vr_deadline || null;
        STATE.uploadedDocs = Array.isArray(d.documents) ? [...d.documents] : [];

        const det = d.details || {};
        const set = (elId, val) => { const el = document.getElementById(elId); if (el && val != null && val !== "") el.value = val; };
        set("inp-project_name", det.project_name);
        set("inp-client_org", det.client_org);
        set("inp-site_address", det.site_address);
        set("inp-currency", det.currency);
        set("inp-start_date", det.start_date);
        set("inp-end_date", det.end_date);
        if (det.budget != null) set("inp-budget", det.budget);
        set("inp-description", det.description);

        STATE.projectDetails = { ...det };

        STATE.completedSteps = new Set([1, 2, 3, 4, 5, 6, 7, 8]);

        document.getElementById("projectListView").style.display = "none";
        document.getElementById("wizardView").style.display      = "block";
        applyWizardChrome();
        buildSidebar();
        goToStep(1);
    } catch (e) {
        console.error("[Edit wizard]", e);
        showToast("Could not load project for editing.", "error");
    }
}

// ─────────────────────────────────────────────────────────
// WIZARD ENTRY / EXIT
// ─────────────────────────────────────────────────────────
async function startWizard() {
    STATE.editProjectId = null;
    // Create draft on backend
    try {
        const res = await apiFetch("/api/new-project/draft", { method: "POST" });
        STATE.draftId = res.draft_id;
    } catch {
        showToast("Could not create draft. Please try again.", "error");
        return;
    }

    document.getElementById("projectListView").style.display = "none";
    document.getElementById("wizardView").style.display      = "block";
    document.getElementById("draftIdTag").textContent         = STATE.draftId;
    applyWizardChrome();

    buildSidebar();
    goToStep(1);
}

async function saveDraftAndExit() {
    if (isEditMode()) {
        window.location.href = "/dashboard?project=" + encodeURIComponent(STATE.editProjectId);
        return;
    }

    // Save the current step so we can resume here when the draft is reopened
    localStorage.setItem("draft_lastStep_" + STATE.draftId, STATE.currentStep);

    // Save Step 2 form fields regardless of which step we are on
    const fields = ["project_name","client_org","site_address","start_date","end_date","budget","currency","description"];
    const body   = {};
    fields.forEach(f => { const el = document.getElementById("inp-" + f); if (el) body[f] = el.value; });
    if (body.project_name && body.project_name.trim()) {
        localStorage.setItem("draft_details_" + STATE.draftId, JSON.stringify(body));
        try {
            await apiFetch(`/api/new-project/draft/${STATE.draftId}/details`, {
                method: "PUT",
                body: JSON.stringify(body),
            });
        } catch { /* non-fatal — draft still saved locally */ }
    }

    showToast("Draft saved — " + STATE.draftId, "success");
    setTimeout(() => {
        document.getElementById("wizardView").style.display       = "none";
        document.getElementById("projectListView").style.display  = "block";
        loadProjectList();
    }, 800);
}

// ─────────────────────────────────────────────────────────
// SIDEBAR
// ─────────────────────────────────────────────────────────
function buildSidebar() {
    const list = document.getElementById("stepList");
    list.innerHTML = STEPS.map(s => `
        <div class="step-item" id="sidebar-step-${s.num}" onclick="jumpToStep(${s.num})">
            <div class="step-num" id="step-num-${s.num}">${s.num}</div>
            <div class="step-label">${s.title}</div>
        </div>
    `).join("");
}

function updateSidebar() {
    STEPS.forEach(s => {
        const item = document.getElementById(`sidebar-step-${s.num}`);
        const num  = document.getElementById(`step-num-${s.num}`);
        if (!item) return;

        item.className = "step-item";
        num.textContent = s.num;

        if (s.num === STATE.currentStep) {
            item.classList.add("active");
        } else if (STATE.completedSteps.has(s.num)) {
            item.classList.add("complete");
            num.textContent = "✓";
        }
    });
}

function jumpToStep(num) {
    if (STATE.completedSteps.has(num) || num === STATE.currentStep) {
        goToStep(num);
    }
}

// ─────────────────────────────────────────────────────────
// NAVIGATION
// ─────────────────────────────────────────────────────────
function goToStep(num) {
    STATE.currentStep = num;

    // Hide all panels, show current
    document.querySelectorAll(".step-panel").forEach(p => p.classList.remove("active"));
    const panel = document.getElementById(`panel-${num}`);
    if (panel) panel.classList.add("active");

    // Update header
    const step = STEPS[num - 1];
    document.getElementById("stepBadge").textContent = `STEP ${num} OF 10`;
    document.getElementById("stepTitle").textContent  = step.title;
    document.getElementById("stepDesc").textContent   = step.desc;

    // Footer buttons
    document.getElementById("btnBack").style.display = num <= 1 ? "none" : "inline-flex";
    document.getElementById("btnNext").style.display = num >= 10 ? "none" : "inline-flex";
    document.getElementById("btnSkip").style.display = step.skippable ? "inline-flex" : "none";

    if (num === 9) {
        document.getElementById("btnNext").textContent = isEditMode() ? "Save changes →" : "Publish Project →";
    } else if (num < 9) {
        document.getElementById("btnNext").textContent = "Next →";
    }

    updateSidebar();

    // Step-specific loaders
    if (num === 1) loadStep1();
    if (num === 3) loadStep3();
    if (num === 4) loadStep4();
    if (num === 5) loadStep5();
    if (num === 6) loadStep6();
    if (num === 7) loadStep7();
    if (num === 8) loadStep8();
    if (num === 9) loadStep9();
}

async function nextStep() {
    const valid = await validateAndSaveStep(STATE.currentStep);
    if (!valid) return;
    STATE.completedSteps.add(STATE.currentStep);
    const next = STATE.currentStep + 1;
    if (next === 10) return; // already handled by publish
    goToStep(next);
}

function prevStep() {
    if (STATE.currentStep > 1) goToStep(STATE.currentStep - 1);
}

function skipStep() {
    STATE.completedSteps.add(STATE.currentStep);
    goToStep(STATE.currentStep + 1);
}

// ─────────────────────────────────────────────────────────
// STEP 1 — SELECT BUILDING TYPE  (UC-09.2)
// ─────────────────────────────────────────────────────────
async function loadStep1() {
    if (STATE.buildingTypes.length > 0) {
        renderCategoryList();
        return;
    }
    try {
        const res = await apiFetch("/api/new-project/building-types");
        STATE.buildingTypes = res.data;
        renderCategoryList();
    } catch {
        showToast("Could not load building types.", "error");
    }
}

function renderCategoryList() {
    const categories = [...new Set(STATE.buildingTypes.map(b => b.category))];
    const catList    = document.getElementById("categoryList");
    catList.innerHTML = `
        <button class="cat-btn active" onclick="filterCategory(this, 'All')">All Types</button>
        ${categories.map(c => `<button class="cat-btn" onclick="filterCategory(this,'${c}')">${c}</button>`).join("")}
    `;
    renderTypeGrid("All");
}

function filterCategory(btn, cat) {
    document.querySelectorAll(".cat-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    renderTypeGrid(cat);
}

function renderTypeGrid(cat) {
    const types = cat === "All" ? STATE.buildingTypes : STATE.buildingTypes.filter(b => b.category === cat);
    const grid  = document.getElementById("typeGrid");
    grid.innerHTML = types.map(bt => `
        <div class="type-card ${STATE.selectedBuildingType?.id === bt.id ? 'selected' : ''}" onclick="selectBuildingType('${bt.id}')">
            <div class="type-card-check">✓</div>
            <div class="type-card-icon">${bt.icon}</div>
            <div class="type-card-name">${bt.name}</div>
            <div class="type-card-desc">${bt.description}</div>
        </div>
    `).join("");
}

async function selectBuildingType(id) {
    STATE.selectedBuildingType = STATE.buildingTypes.find(b => b.id === id);
    if (STATE.draftId && !isEditMode()) localStorage.setItem("draft_buildingType_" + STATE.draftId, JSON.stringify(STATE.selectedBuildingType));
    renderTypeGrid(document.querySelector(".cat-btn.active")?.textContent === "All Types" ? "All" : document.querySelector(".cat-btn.active")?.textContent || "All");

    // Fetch AI summary
    try {
        const res = await apiFetch(`/api/new-project/building-types/${id}`);
        const s   = res.data.ai_summary;
        const panel = document.getElementById("aiSummaryPanel");
        panel.style.display = "block";
        panel.innerHTML = `
            <div class="ai-summary-panel">
                <h4>🤖 AI Template Summary — ${res.data.name}</h4>
                <div class="ai-summary-stats">
                    <div class="ai-stat"><span class="ai-stat-value">${s.task_count}</span><span class="ai-stat-label">Tasks</span></div>
                    <div class="ai-stat"><span class="ai-stat-value">${s.zone_count}</span><span class="ai-stat-label">Zones</span></div>
                    <div class="ai-stat"><span class="ai-stat-value">${s.vr_count}</span><span class="ai-stat-label">VR Modules</span></div>
                    <div class="ai-stat"><span class="ai-stat-value">${s.resource_count}</span><span class="ai-stat-label">Resources</span></div>
                    <div class="ai-stat"><span class="ai-stat-value">${s.safety_rules}</span><span class="ai-stat-label">Safety Rules</span></div>
                </div>
            </div>
        `;
    } catch {}
}

// ─────────────────────────────────────────────────────────
// STEP 3 — SITE ZONES  (UC-09.4)
// ─────────────────────────────────────────────────────────
async function loadStep3() {
    // Zones are pre-seeded from building type selection
    if (STATE.zones.length === 0) {
        try {
            if (isEditMode()) {
                const r = await apiFetch(`/api/new-project/active/${encodeURIComponent(STATE.editProjectId)}`);
                STATE.zones = r.data.zones || [];
            } else {
                const draft = await apiFetch(`/api/new-project/draft/${STATE.draftId}`);
                STATE.zones = draft.data.zones || [];
            }
        } catch {}
    }

    const buildingName = STATE.selectedBuildingType?.name || "selected template";
    document.getElementById("zonesBuildingName").textContent = buildingName;

    renderZoneMap();
    renderZoneList();
}

function renderZoneMap() {
    const map = document.getElementById("zoneMap");
    if (STATE.zones.length === 0) {
        map.innerHTML = `<div class="zone-map-placeholder"><div class="map-icon">🗺️</div><p>No zones defined</p></div>`;
        return;
    }

    const cells = STATE.zones.slice(0, 8).map((z, i) => `
        <div class="zone-map-cell" style="background:${ZONE_COLOURS[i % ZONE_COLOURS.length]}33; border: 2px solid ${ZONE_COLOURS[i % ZONE_COLOURS.length]}66;"
             title="${z.name}">
            ${z.name}
        </div>
    `).join("");

    map.innerHTML = `<div class="zone-map-grid">${cells}</div>`;
}

function renderZoneList() {
    const panel = document.getElementById("zoneListPanel");
    panel.innerHTML = STATE.zones.map((z, i) => `
        <div class="zone-item" id="zone-item-${i}">
            <div class="zone-dot" style="background:${ZONE_COLOURS[i % ZONE_COLOURS.length]}"></div>
            <input class="zone-item-name" value="${z.name}" 
                   style="background:transparent;border:none;color:var(--text);font-family:var(--font);font-size:0.85rem;flex:1;"
                   onchange="updateZoneName(${i}, this.value)">
            <span class="zone-cam-tag">${z.camera}</span>
            <button class="zone-del-btn" onclick="deleteZone(${i})">✕</button>
        </div>
    `).join("");
}

function updateZoneName(i, name) {
    STATE.zones[i].name = name;
    renderZoneMap();
}

function deleteZone(i) {
    STATE.zones.splice(i, 1);
    // Renumber
    STATE.zones = STATE.zones.map((z, idx) => ({ ...z, id: `Z${idx+1}`, camera: `CAM-${String(idx+1).padStart(2,'0')}` }));
    if (STATE.zones.length > 8) {
        showToast("Warning: Maximum 8 monitoring zones recommended.", "error");
    }
    renderZoneMap();
    renderZoneList();
}

function addZone() {
    const i = STATE.zones.length;
    STATE.zones.push({ id: `Z${i+1}`, name: `Zone ${i+1} — New`, camera: `CAM-${String(i+1).padStart(2,'0')}` });
    renderZoneMap();
    renderZoneList();
    if (STATE.zones.length > 8) {
        showToast("Maximum 8 monitoring zones recommended.", "error");
    }
}

// ─────────────────────────────────────────────────────────
// STEP 4 — TEAM MEMBERS  (UC-09.5)
// ─────────────────────────────────────────────────────────
async function refreshUserDirectory() {
    try {
        const res = await apiFetch("/api/new-project/users");
        if (Array.isArray(res.data)) STATE.userDirectory = res.data;
    } catch {}
}

async function loadStep4() {
    await refreshUserDirectory();
    renderTeamPanel();
    // Set up drag-drop on file input (for docs step while we're here)
    setupDocUpload();
}

function openAddDirectoryUserModal() {
    document.getElementById("addDirUserName").value = "";
    document.getElementById("addDirUserEmail").value = "";
    document.getElementById("addDirUserRole").value = "Student";
    document.getElementById("addDirUserAvatar").value = "";
    document.getElementById("addDirectoryUserModal").classList.remove("hidden");
}

function closeAddDirectoryUserModal() {
    document.getElementById("addDirectoryUserModal").classList.add("hidden");
}

async function submitAddDirectoryUser() {
    const btn = document.getElementById("btnAddDirectoryUserSubmit");
    const name = document.getElementById("addDirUserName").value.trim();
    const email = document.getElementById("addDirUserEmail").value.trim();
    const role = document.getElementById("addDirUserRole").value;
    const avatarRaw = document.getElementById("addDirUserAvatar").value.trim();
    const body = { name, email, role, avatar: avatarRaw || null };
    btn.disabled = true;
    try {
        const res = await fetch("/api/new-project/users", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            showToast(data.message || "Could not add user", "error");
            return;
        }
        await refreshUserDirectory();
        closeAddDirectoryUserModal();
        showToast("Added to directory", "success");
        searchUsers(document.getElementById("userSearchInput").value);
    } catch {
        showToast("Could not add user", "error");
    } finally {
        btn.disabled = false;
    }
}

async function searchUsers(query) {
    const results = document.getElementById("userResults");

    if (!query.trim()) {
        results.innerHTML = `<div style="padding:1rem;color:var(--muted);font-size:0.82rem;">Type to search…</div>`;
        return;
    }

    const q      = query.toLowerCase();
    const found  = STATE.userDirectory.filter(u =>
        u.name.toLowerCase().includes(q) ||
        u.id.toLowerCase().includes(q) ||
        u.email.toLowerCase().includes(q)
    );

    if (!found.length) {
        results.innerHTML = `<div style="padding:1rem;color:var(--muted);font-size:0.82rem;">No users found.</div>`;
        return;
    }

    results.innerHTML = found.map(u => {
        const alreadyAdded = STATE.teamMembers.some(m => m.id === u.id);
        return `
        <div class="user-result-item">
            <div class="user-avatar">${u.avatar ? `<img src="${u.avatar}">` : u.name.charAt(0)}</div>
            <div class="user-info">
                <div class="user-name">${u.name}</div>
                <div class="user-role-tag">${u.role} · ${u.email}</div>
            </div>
            ${alreadyAdded
                ? `<span style="font-size:0.72rem;color:var(--green);">Added ✓</span>`
                : `<button class="add-user-btn" onclick='addTeamMember(${JSON.stringify(u)})'>Add</button>`
            }
        </div>`;
    }).join("");
}

function saveTeamToLocal() {
    if (STATE.draftId && !isEditMode()) localStorage.setItem("draft_team_" + STATE.draftId, JSON.stringify(STATE.teamMembers));
}

function addTeamMember(user) {
    if (STATE.teamMembers.some(m => m.id === user.id)) return;
    STATE.teamMembers.push({ ...user, role: user.role });
    saveTeamToLocal();
    renderTeamPanel();
    searchUsers(document.getElementById("userSearchInput").value);
}

function removeTeamMember(id) {
    STATE.teamMembers = STATE.teamMembers.filter(m => m.id !== id);
    saveTeamToLocal();
    renderTeamPanel();
    searchUsers(document.getElementById("userSearchInput").value);
}

function updateMemberRole(id, newRole) {
    const m = STATE.teamMembers.find(m => m.id === id);
    if (m) { m.role = newRole; checkTeamGaps(); }
}

const ROLE_OPTIONS = ["Student", "Safety Officer", "Site Foreman", "Instructor / PM", "Observer"];

function renderTeamPanel() {
    const panel = document.getElementById("teamPanel");
    if (STATE.teamMembers.length === 0) {
        panel.innerHTML = `<div style="color:var(--muted);font-size:0.82rem;padding:0.5rem;">No members added yet.</div>`;
    } else {
        panel.innerHTML = STATE.teamMembers.map(m => `
            <div class="team-member-item">
                <div class="user-avatar" style="width:26px;height:26px;font-size:0.65rem;">${m.avatar ? `<img src="${m.avatar}">` : m.name.charAt(0)}</div>
                <div class="team-member-name">${m.name}</div>
                <select class="role-select" onchange="updateMemberRole('${m.id}', this.value)">
                    ${ROLE_OPTIONS.map(r => `<option ${r===m.role?"selected":""}>${r}</option>`).join("")}
                </select>
                <button class="remove-member-btn" onclick="removeTeamMember('${m.id}')">✕</button>
            </div>
        `).join("");
    }
    checkTeamGaps();
}

function checkTeamGaps() {
    const roles = STATE.teamMembers.map(m => m.role);
    const gaps  = [];
    if (!roles.includes("Safety Officer")) gaps.push("No Safety Officer assigned — required for this building type.");

    const gapPanel  = document.getElementById("teamGapsPanel");
    const roleSumEl = document.getElementById("roleSummary");

    gapPanel.innerHTML = gaps.length
        ? `<div class="gap-alert">⚠️ ${gaps.join("<br>")}</div>`
        : "";

    const counts = {};
    roles.forEach(r => counts[r] = (counts[r] || 0) + 1);
    roleSumEl.textContent = Object.entries(counts).map(([r,c]) => `${c}× ${r}`).join("  ·  ") || "";
}

// ─────────────────────────────────────────────────────────
// STEP 5 — RESOURCE PLAN / GANTT  (UC-09.6) — core CPM in static/js/gantt_cpm.js
// ─────────────────────────────────────────────────────────
function _parseISODateUTC(iso) {
    return VeritasGantt.parseISODateUTC(iso);
}

function _daysBetweenUTC(a, b) {
    return VeritasGantt.daysBetweenUTC(a, b);
}

function firstWorkingDayOnOrAfterISO(iso) {
    return VeritasGantt.firstWorkingDayOnOrAfterISO(iso);
}

function addWorkingDaysForwardISO(anchorISO, steps) {
    return VeritasGantt.addWorkingDaysForwardISO(anchorISO, steps);
}

function workingDayStartFromOffset(anchorISO, offsetWd) {
    return VeritasGantt.workingDayStartFromOffset(anchorISO, offsetWd);
}

function taskLastDayFromStartAndWdDuration(startISO, durWd) {
    return VeritasGantt.taskLastDayFromStartAndWdDuration(startISO, durWd);
}

function minStartWForUserCalendarDate(anchorISO, userDateISO) {
    return VeritasGantt.minStartWForUserCalendarDate(anchorISO, userDateISO);
}

function inferResourcePoolJs(name) {
    return VeritasGantt.inferResourcePoolJs(name);
}

function taskResourcePool(t) {
    return VeritasGantt.taskResourcePool(t);
}

function depLagWd(t, depArrayIndex) {
    return VeritasGantt.depLagWd(t, depArrayIndex);
}

function getWizardProjectSpanDays() {
    const pd = STATE.projectDetails;
    let startS = pd?.start_date;
    let endS = pd?.end_date;
    if (!startS || !endS) {
        startS = document.getElementById("inp-start_date")?.value;
        endS = document.getElementById("inp-end_date")?.value;
    }
    return VeritasGantt.getSpanFromDates(startS, endS);
}

/** Full span for CPM when project dates are incomplete (synthetic window from a task start). */
function wizardSpanForCPM(fallbackStartISO) {
    let span = getWizardProjectSpanDays();
    if (span.startISO && span.endISO) return span;
    const anchor = VeritasGantt.firstWorkingDayOnOrAfterISO(String(fallbackStartISO || "").slice(0, 10));
    const pd = STATE.projectDetails;
    let endISO = (pd && pd.end_date) || document.getElementById("inp-end_date")?.value || "";
    if (!endISO) {
        const a = VeritasGantt.parseISODateUTC(anchor);
        if (a) {
            const e = new Date(a.getTime());
            e.setUTCDate(e.getUTCDate() + 365);
            endISO = e.toISOString().slice(0, 10);
        }
    }
    if (!endISO) endISO = anchor;
    if (anchor && endISO) span = VeritasGantt.getSpanFromDates(anchor, endISO);
    return span;
}

const WIZARD_GANTT_HEADER_COLS = 12;
const WIZARD_GANTT_FALLBACK_HEADERS = [
    "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun",
    "Week 2", "Week 3", "Week 4", "Week 5", "Week 6",
];

/** Gantt header: one date per column along project start→end (12 segments, same scale as bar %). */
function buildWizardGanttHeaderRowHtml() {
    const span = getWizardProjectSpanDays();
    const VG = VeritasGantt;
    if (!span.startISO || !span.endISO || span.totalDays < 1) {
        return WIZARD_GANTT_FALLBACK_HEADERS.map(d =>
            `<div class="gantt-day-label gantt-day-label--simple">${_escapeHtmlGantt(d)}</div>`
        ).join("");
    }
    const projStart = VG.parseISODateUTC(String(span.startISO).slice(0, 10));
    if (!projStart) {
        return WIZARD_GANTT_FALLBACK_HEADERS.map(d =>
            `<div class="gantt-day-label gantt-day-label--simple">${_escapeHtmlGantt(d)}</div>`
        ).join("");
    }
    const total = Math.max(1, span.totalDays);
    const dowNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    const cells = [];
    for (let c = 0; c < WIZARD_GANTT_HEADER_COLS; c++) {
        const offsetDays = Math.floor((c * total) / WIZARD_GANTT_HEADER_COLS);
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
            `<span class="gantt-hd-dow">${_escapeHtmlGantt(dow)}</span>` +
            `<span class="gantt-hd-date">${_escapeHtmlGantt(dateShort)}</span></div>`
        );
    }
    return cells.join("");
}

/** Predecessor id → row index (TASK-NNN or any task id). */
function _ganttDepIdToIndex(depId) {
    return VeritasGantt.depIdToIndex(STATE.ganttTasks, depId);
}

function _ganttTaskIdAtIndex(tasks, k) {
    return VeritasGantt.taskIdAtIndex(tasks, k);
}

function _escapeHtmlGantt(s) {
    return String(s == null ? "" : s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function ganttGraphHasCycle(tasks) {
    return VeritasGantt.ganttGraphHasCycle(tasks);
}

function recalculateGanttCPM(tasks, opts = {}, spanOverride = null) {
    const span = spanOverride || getWizardProjectSpanDays();
    return VeritasGantt.recalculateGanttCPM(tasks, span, opts);
}

function syncGanttTaskLayout(task) {
    const span = getWizardProjectSpanDays();
    VeritasGantt.syncGanttTaskLayout(task, span);
}

function persistGanttToLocal() {
    const key = isEditMode() ? `edit_gantt_${STATE.editProjectId}` : `draft_gantt_${STATE.draftId}`;
    if (!STATE.draftId && !STATE.editProjectId) return;
    try {
        localStorage.setItem(key, JSON.stringify(STATE.ganttTasks));
    } catch (_) {}
}

function refreshGanttEditEndPreview() {
    const start = document.getElementById("ganttEditStart")?.value;
    const durEl = document.getElementById("ganttEditDuration");
    const out = document.getElementById("ganttEditEndDisplay");
    if (!start || !durEl || !out) return;
    const dur = Math.max(1, parseInt(durEl.value, 10) || 1);
    const s0 = firstWorkingDayOnOrAfterISO(start);
    out.textContent = taskLastDayFromStartAndWdDuration(s0, dur);
}

function renderGanttEditDepsPanel(taskIndex) {
    const wrap = document.getElementById("ganttEditDeps");
    if (!wrap) return;
    const tasks = STATE.ganttTasks;
    const t = tasks[taskIndex];
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

    const rows = tasks.map((ot, k) => {
        if (k === taskIndex) return "";
        const tid = _ganttTaskIdAtIndex(tasks, k);
        const checked = depList.includes(String(tid)) ? "checked" : "";
        const lag = lagByPred.has(String(tid)) ? lagByPred.get(String(tid)) : 0;
        const safeName = _escapeHtmlGantt(ot.name || tid);
        return `<div class="gantt-dep-row">
      <label class="gantt-dep-label" for="gantt-dep-${k}">
        <input type="checkbox" class="gantt-dep-cb" id="gantt-dep-${k}" ${checked} onchange="toggleGanttDepLag(${k})">
        <span class="gantt-dep-name">${safeName}</span>
        <span class="gantt-dep-id">${tid}</span>
      </label>
      <label class="gantt-dep-lag-wrap">Lag (WD)
        <input type="number" class="form-input gantt-dep-lag" id="gantt-dep-lag-${k}" min="0" step="1" value="${lag}" ${checked ? "" : "disabled"}>
      </label>
    </div>`;
    }).join("");

    wrap.innerHTML = rows.trim()
        ? rows
        : `<div style="color:var(--muted);font-size:0.8rem;">No other tasks.</div>`;
}

function toggleGanttDepLag(k) {
    const cb = document.getElementById(`gantt-dep-${k}`);
    const lag = document.getElementById(`gantt-dep-lag-${k}`);
    if (cb && lag) {
        lag.disabled = !cb.checked;
        if (!cb.checked) lag.value = "0";
    }
}

function collectGanttEditDeps(taskIndex) {
    const tasks = STATE.ganttTasks;
    const pairs = [];
    for (let k = 0; k < tasks.length; k++) {
        if (k === taskIndex) continue;
        const cb = document.getElementById(`gantt-dep-${k}`);
        if (cb && cb.checked) {
            const tid = _ganttTaskIdAtIndex(tasks, k);
            const lagEl = document.getElementById(`gantt-dep-lag-${k}`);
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

function syncGanttEditInspectionFields() {
    const cb = document.getElementById("ganttEditInspectionNeeded");
    const dateEl = document.getElementById("ganttEditInspectionDate");
    const lbl = document.getElementById("ganttEditInspectionDateLabel");
    if (!cb || !dateEl) return;
    const on = cb.checked;
    dateEl.disabled = !on;
    if (!on) dateEl.value = "";
    if (lbl) lbl.style.opacity = on ? "1" : "0.5";
}

function openGanttTaskEditor(taskIndex) {
    const t = STATE.ganttTasks[taskIndex];
    if (!t) return;
    ganttEditTaskIndex = taskIndex;
    document.getElementById("ganttEditName").value = t.name || "";
    document.getElementById("ganttEditStart").value = t.start_date || "";
    document.getElementById("ganttEditDuration").value = String(t.duration ?? 1);
    const costEl = document.getElementById("ganttEditCost");
    if (costEl) costEl.value = String(Number.isFinite(Number(t.cost)) ? Number(t.cost) : "");
    const inspCb = document.getElementById("ganttEditInspectionNeeded");
    const inspDate = document.getElementById("ganttEditInspectionDate");
    if (inspCb) {
        const ir = t.inspection_required;
        inspCb.checked = ir === true || ir === 1 || ir === "1" || String(ir).toLowerCase() === "true";
    }
    if (inspDate) {
        const id = t.inspection_date;
        inspDate.value = id ? String(id).slice(0, 10) : "";
    }
    syncGanttEditInspectionFields();
    refreshGanttEditEndPreview();
    renderGanttEditDepsPanel(taskIndex);
    document.getElementById("ganttTaskEditModal").classList.remove("hidden");
}

function closeGanttTaskEditModal() {
    document.getElementById("ganttTaskEditModal").classList.add("hidden");
    ganttEditTaskIndex = -1;
}

function saveGanttTaskEdit() {
    const i = ganttEditTaskIndex;
    if (i < 0 || i >= STATE.ganttTasks.length) {
        closeGanttTaskEditModal();
        return;
    }
    const name = document.getElementById("ganttEditName").value.trim();
    const start = document.getElementById("ganttEditStart").value;
    const dur = parseInt(document.getElementById("ganttEditDuration").value, 10);
    const costRaw = document.getElementById("ganttEditCost")?.value?.trim() ?? "";
    const parsedCost = costRaw === "" ? null : Number(costRaw);
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
    if (costRaw !== "" && (!Number.isFinite(parsedCost) || parsedCost < 0)) {
        showToast("Task cost must be a non-negative number.", "error");
        return;
    }
    const { startISO, endISO } = getWizardProjectSpanDays();
    if (startISO && _daysBetweenUTC(_parseISODateUTC(startISO), _parseISODateUTC(start)) < 0) {
        showToast("Start date cannot be before the project start.", "error");
        return;
    }
    const task = STATE.ganttTasks[i];
    const prevDeps = Array.isArray(task.deps) ? [...task.deps] : [];
    const prevLags = Array.isArray(task.dep_lag_wd) ? [...task.dep_lag_wd] : [];
    const built = collectGanttEditDeps(i);
    task.deps = built.deps;
    task.dep_lag_wd = built.dep_lag_wd;
    if (ganttGraphHasCycle(STATE.ganttTasks)) {
        task.deps = prevDeps;
        task.dep_lag_wd = prevLags;
        renderGanttEditDepsPanel(i);
        showToast("Those predecessors create a cycle in the task graph. Change the links and try again.", "error");
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

    const inspCb = document.getElementById("ganttEditInspectionNeeded");
    const inspDateEl = document.getElementById("ganttEditInspectionDate");
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

    const span = wizardSpanForCPM(start);
    const cpm = recalculateGanttCPM(STATE.ganttTasks, {
        editedIndex: i,
        editedMinStartISO: start,
    }, span);
    if (cpm.overriddenStart) {
        showToast("Start date moved later to satisfy task dependencies.", "info");
    }
    if (cpm.anyPastProjectEnd) {
        showToast("Some tasks now end after the project end date — adjust if needed.", "info");
    }
    persistGanttToLocal();
    renderGantt();
    closeGanttTaskEditModal();
    showToast("Schedule updated (working-day CPM + leveling)", "success");
}

async function loadStep5() {
    const lsKey = isEditMode() ? `edit_gantt_${STATE.editProjectId}` : `draft_gantt_${STATE.draftId}`;
    if (!isEditMode()) {
        const saved = localStorage.getItem(lsKey);
        if (saved) {
            STATE.ganttTasks = JSON.parse(saved);
            renderGantt();
            return;
        }
    }

    const container = document.getElementById("ganttContainer");
    container.innerHTML = `<div class="loading-spinner"><div class="spinner"></div> Generating AI schedule…</div>`;
    try {
        const res = await apiFetch(`${wizardApiBase()}/gantt`);
        STATE.ganttTasks = res.data;
        localStorage.setItem(lsKey, JSON.stringify(STATE.ganttTasks));
        renderGantt();
    } catch(e) {
        container.innerHTML = `<p style="color:var(--red);">Could not generate Gantt. Ensure building type and dates are set.</p>`;
    }
}

function wizardGanttBarPointerDown(e, taskIndex) {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    const bar = e.currentTarget;
    const track = bar.closest(".gantt-track");
    if (!track || !STATE.ganttTasks[taskIndex]) return;
    e.stopPropagation();
    e.preventDefault();
    const t = STATE.ganttTasks[taskIndex];
    const initialLeftPct = Number(t.start_offset_pct) || 0;
    const widthPct = Math.max(Number(t.width_pct) || 3, 0.3);
    wizardGanttDrag = {
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
    bar.addEventListener("pointermove", wizardGanttBarPointerMove);
    bar.addEventListener("pointerup", wizardGanttBarPointerUp);
    bar.addEventListener("pointercancel", wizardGanttBarPointerUp);
}

function wizardGanttBarPointerMove(e) {
    const st = wizardGanttDrag;
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

function wizardGanttBarPointerUp(e) {
    const st = wizardGanttDrag;
    if (!st || e.pointerId !== st.pointerId) return;
    wizardGanttDrag = null;
    const bar = st.bar;
    bar.style.transition = "";
    bar.classList.remove("gantt-bar-dragging");
    bar.removeEventListener("pointermove", wizardGanttBarPointerMove);
    bar.removeEventListener("pointerup", wizardGanttBarPointerUp);
    bar.removeEventListener("pointercancel", wizardGanttBarPointerUp);
    try {
        bar.releasePointerCapture(e.pointerId);
    } catch (_) { /* ignore */ }

    if (!st.moved) {
        openGanttTaskEditor(st.index);
        return;
    }
    const tw = st.track.getBoundingClientRect().width;
    if (tw < 1) {
        renderGantt();
        return;
    }
    const m = bar.style.left.match(/([\d.]+)%/);
    let leftPct = m ? parseFloat(m[1]) : st.initialLeftPct;
    leftPct = Math.max(0, Math.min(100 - st.widthPct, leftPct));
    commitWizardGanttBarDrag(st.index, leftPct);
}

function commitWizardGanttBarDrag(taskIndex, leftPct) {
    const span0 = getWizardProjectSpanDays();
    if (!span0.startISO || span0.totalDays < 1) {
        showToast("Set project start and end dates (step 2) to reschedule from the chart.", "error");
        renderGantt();
        return;
    }
    const proj = VeritasGantt.parseISODateUTC(String(span0.startISO).slice(0, 10));
    if (!proj) {
        renderGantt();
        return;
    }
    const total = Math.max(1, span0.totalDays);
    const offsetDays = Math.round((leftPct / 100) * total);
    const d = new Date(proj.getTime());
    d.setUTCDate(d.getUTCDate() + offsetDays);
    let minISO = d.toISOString().slice(0, 10);
    minISO = VeritasGantt.firstWorkingDayOnOrAfterISO(minISO);
    if (span0.startISO && VeritasGantt.daysBetweenUTC(VeritasGantt.parseISODateUTC(span0.startISO), VeritasGantt.parseISODateUTC(minISO)) < 0) {
        minISO = String(span0.startISO).slice(0, 10);
    }

    const span = wizardSpanForCPM(minISO);
    if (!span.startISO) {
        renderGantt();
        return;
    }

    const cpm = recalculateGanttCPM(STATE.ganttTasks, {
        editedIndex:       taskIndex,
        editedMinStartISO: minISO,
    }, span);
    if (cpm.overriddenStart) {
        showToast("Start adjusted for dependencies or crew leveling.", "info");
    }
    if (cpm.anyPastProjectEnd) {
        showToast("Some tasks extend past the project end date.", "info");
    }
    persistGanttToLocal();
    renderGantt();
    showToast("Task rescheduled", "success");
}

function renderGantt() {
    const container = document.getElementById("ganttContainer");
    const tasks     = STATE.ganttTasks;
    if (!tasks.length) {
        container.innerHTML = `<p style="color:var(--muted);">No tasks generated.</p>`;
        return;
    }

    container.innerHTML = `
        <div class="gantt-wrap">
            <div class="gantt-header-row">
                ${buildWizardGanttHeaderRowHtml()}
            </div>
            ${tasks.map((t, i) => `
                <div class="gantt-task-row">
                    <div class="gantt-task-label" title="${t.name}">${t.name}</div>
                    <div class="gantt-track">
                        <div class="gantt-bar gantt-bar--draggable" role="button" tabindex="0"
                            title="Drag to reschedule, or click to edit"
                            style="left:${t.start_offset_pct}%;width:${Math.max(t.width_pct,3)}%;background:${i%2===0?'var(--blue)':'var(--green)'}"
                            onpointerdown="wizardGanttBarPointerDown(event, ${i})"
                            onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openGanttTaskEditor(${i});}">
                            ${t.duration}d
                        </div>
                    </div>
                </div>
            `).join("")}
        </div>
        <div style="margin-top:1rem;font-size:0.78rem;color:var(--muted);">
            ${tasks.length} task(s) · Mon–Fri working days, lag &amp; crew pools — drag or edit re-runs CPM and levels shared resources
        </div>
        <div style="margin-top:1rem;">
            <button class="btn-secondary" onclick="regenGantt()">↺ Regenerate</button>
        </div>
    `;
}

async function regenGantt() {
    await loadStep5();
}

// ─────────────────────────────────────────────────────────
// STEP 6 — SAFETY PROTOCOLS  (UC-09.7)
// ─────────────────────────────────────────────────────────
async function loadStep6() {
    const lsKey = isEditMode() ? `edit_safety_${STATE.editProjectId}` : `draft_safety_${STATE.draftId}`;
    const saved = localStorage.getItem(lsKey);
    if (saved) { const p = JSON.parse(saved); STATE.safetyRules = p.rules; renderSafetyRules(p.officer); return; }

    const container = document.getElementById("safetyRuleContainer");
    container.innerHTML = `<div class="loading-spinner"><div class="spinner"></div> Loading safety protocols…</div>`;
    try {
        const res = await apiFetch(`${wizardApiBase()}/safety`);
        STATE.safetyRules = res.data;
        localStorage.setItem(lsKey, JSON.stringify({ rules: res.data, officer: res.safety_officer }));
        renderSafetyRules(res.safety_officer);
    } catch {
        container.innerHTML = `<p style="color:var(--red);">Could not load safety protocols.</p>`;
    }
}

function renderSafetyRules(officer) {
    const container = document.getElementById("safetyRuleContainer");
    if (!STATE.safetyRules.length) {
        container.innerHTML = `<p style="color:var(--muted);">No safety protocols loaded.</p>`;
        return;
    }

    container.innerHTML = `
        <div style="margin-bottom:1rem;font-size:0.82rem;color:var(--muted);">
            Safety Officer: <strong style="color:var(--text)">${officer}</strong> — will receive push alerts for all Critical-severity rules.
        </div>
        <div class="safety-rule-list">
            ${STATE.safetyRules.map((rule, i) => `
                <div class="safety-rule-item">
                    <div>
                        <div class="safety-rule-code">${rule.code}</div>
                        <div class="safety-rule-text">${rule.rule}</div>
                        <div class="safety-rule-zones">
                            ${rule.zone_names.map(z => `<span class="zone-tag">${z}</span>`).join("")}
                        </div>
                    </div>
                    <div class="rule-toggle">
                        <div class="toggle-switch ${rule.enabled ? '' : 'off'}" onclick="toggleRule(${i})" id="toggle-${i}">
                            <div class="toggle-knob"></div>
                        </div>
                    </div>
                </div>
            `).join("")}
        </div>
    `;
}

function toggleRule(i) {
    STATE.safetyRules[i].enabled = !STATE.safetyRules[i].enabled;
    const toggle = document.getElementById(`toggle-${i}`);
    if (toggle) toggle.classList.toggle("off", !STATE.safetyRules[i].enabled);
}

// ─────────────────────────────────────────────────────────
// STEP 7 — VR TRAINING ASSIGNMENTS  (UC-09.8)
// ─────────────────────────────────────────────────────────
async function loadStep7() {
    const container = document.getElementById("vrMatrixContainer");
    container.innerHTML = `<div class="loading-spinner"><div class="spinner"></div> Generating VR matrix…</div>`;

    try {
        const res = await apiFetch(`${wizardApiBase()}/vr`);
        STATE.vrMatrix   = res.data;
        const vdKey = isEditMode() ? `edit_vrDeadline_${STATE.editProjectId}` : `draft_vrDeadline_${STATE.draftId}`;
        STATE.vrDeadline = localStorage.getItem(vdKey) || res.compliance_deadline;
        renderVRMatrix(STATE.vrDeadline, res.compliance_status);
    } catch {
        container.innerHTML = `<p style="color:var(--red);">Could not generate VR matrix. Ensure team is assigned first.</p>`;
    }
}

function onVrDeadlineChange(el) {
    STATE.vrDeadline = el.value;
    const k = isEditMode() ? `edit_vrDeadline_${STATE.editProjectId}` : `draft_vrDeadline_${STATE.draftId}`;
    if (STATE.draftId || STATE.editProjectId) localStorage.setItem(k, el.value);
}

function renderVRMatrix(deadline, status) {
    const container = document.getElementById("vrMatrixContainer");
    const matrix    = STATE.vrMatrix;

    if (!matrix.length) {
        container.innerHTML = `<p style="color:var(--muted);">No team members assigned yet. Complete Step 4 first.</p>`;
        return;
    }

    // Collect all unique modules
    const modules = matrix[0]?.modules || [];

    const ASSIGNMENT_CYCLE = ["Mandatory", "Recommended", "Not Required"];

    function pillClass(a) {
        if (a === "Mandatory")    return "pill-mandatory";
        if (a === "Recommended")  return "pill-recommended";
        return "pill-not-required";
    }

    container.innerHTML = `
        <div style="margin-bottom:1rem;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.5rem;">
            <div style="font-size:0.82rem;color:var(--muted);">Compliance deadline: <strong style="color:var(--orange)">${deadline}</strong></div>
            <div style="font-size:0.78rem;color:var(--muted);">${status}</div>
        </div>
        <div style="overflow-x:auto;">
        <table class="vr-matrix-table">
            <thead>
                <tr>
                    <th class="name-col">Team Member</th>
                    <th>Role</th>
                    ${modules.map(m => `<th title="${m.title}">${m.title.split(" ").slice(0,2).join(" ")}</th>`).join("")}
                </tr>
            </thead>
            <tbody>
                ${matrix.map((member, mi) => `
                    <tr>
                        <td style="text-align:left;font-weight:500;">${member.name}</td>
                        <td style="font-size:0.72rem;color:var(--muted);">${member.role}</td>
                        ${member.modules.map((mod, moi) => `
                            <td>
                                <span class="assignment-pill ${pillClass(mod.assignment)}"
                                      onclick="cycleAssignment(${mi}, ${moi})"
                                      id="vr-pill-${mi}-${moi}">
                                    ${mod.assignment === "Not Required" ? "—" : mod.assignment}
                                </span>
                            </td>
                        `).join("")}
                    </tr>
                `).join("")}
            </tbody>
        </table>
        </div>
        <div style="margin-top:1rem;">
            <label class="form-label">Compliance Deadline</label>
            <input type="date" class="form-input" style="max-width:200px;margin-top:4px;" value="${deadline}" 
                   onchange="onVrDeadlineChange(this)">
        </div>
    `;
}

function cycleAssignment(memberIdx, moduleIdx) {
    const CYCLE = ["Mandatory", "Recommended", "Not Required"];
    const mod   = STATE.vrMatrix[memberIdx].modules[moduleIdx];
    const ci    = CYCLE.indexOf(mod.assignment);
    mod.assignment = CYCLE[(ci + 1) % CYCLE.length];

    const pill = document.getElementById(`vr-pill-${memberIdx}-${moduleIdx}`);
    if (pill) {
        const classes = {"Mandatory":"pill-mandatory","Recommended":"pill-recommended","Not Required":"pill-not-required"};
        pill.className = `assignment-pill ${classes[mod.assignment]}`;
        pill.textContent = mod.assignment === "Not Required" ? "—" : mod.assignment;
    }
}

// ─────────────────────────────────────────────────────────
// STEP 8 — DOCUMENTS  (UC-09.9)
// ─────────────────────────────────────────────────────────
function loadStep8() {
    // Recommended docs based on building type
    const docs = [
        { icon: "📐", label: "Structural Blueprints (PDF)", category: "Blueprints" },
        { icon: "🦺", label: "Site Safety Plan (DOCX)", category: "Safety" },
        { icon: "📄", label: "Permit Application (PDF)", category: "Permits" },
        { icon: "📋", label: "Material Specification Sheet (PDF)", category: "Materials" },
    ];
    document.getElementById("docRecList").innerHTML = docs.map(d =>
        `<div class="doc-rec-item"><span class="doc-rec-icon">${d.icon}</span>${d.label}</div>`
    ).join("");

    setupDocUpload();   // ensure listeners are attached even when resuming directly to step 8
    renderUploadedDocs();
}

let _docUploadInitialised = false;   // guard against duplicate listeners
function setupDocUpload() {
    if (_docUploadInitialised) return;
    const zone      = document.getElementById("dropZone");
    const fileInput = document.getElementById("fileInput");
    if (!zone || !fileInput) return;
    _docUploadInitialised = true;

    zone.addEventListener("click", () => fileInput.click());

    fileInput.addEventListener("change", e => {
        handleFiles(Array.from(e.target.files));
    });

    zone.addEventListener("dragover", e => { e.preventDefault(); zone.classList.add("drag-over"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
    zone.addEventListener("drop", e => {
        e.preventDefault();
        zone.classList.remove("drag-over");
        handleFiles(Array.from(e.dataTransfer.files));
    });
}

function handleFiles(files) {
    files.forEach(async file => {
        const ext = file.name.split(".").pop().toUpperCase();
        const docId = "DOC-" + Math.random().toString(36).slice(2,8).toUpperCase();
        const doc = {
            doc_id:       docId,
            name:         file.name,
            type:         ext,
            category:     guessCategory(ext),
            size_kb:      Math.round(file.size / 1024),
            version_note: "",
            file_url:     null,
        };
        STATE.uploadedDocs.push(doc);
        renderUploadedDocs();

        try {
            if (ext === "IFC") {
                // IFC files: upload the actual binary so the dashboard can render it
                const formData = new FormData();
                formData.append("file",   file);
                formData.append("doc_id", docId);
                if (STATE.draftId) formData.append("draft_id", STATE.draftId);
                if (STATE.editProjectId) formData.append("project_id", STATE.editProjectId);

                const res = await fetch(`${wizardApiBase()}/documents/upload-ifc`, { method: "POST", body: formData });
                if (!res.ok) throw new Error("Upload failed: " + res.status);
                const json = await res.json();

                // Store the returned URL on the doc so the dashboard can find it
                doc.file_url  = json.file_url;
                doc.doc_id    = json.doc_id || docId;
                renderUploadedDocs();
                showToast("IFC file uploaded — BIM viewer ready.", "success");
            } else {
                // Non-IFC: register metadata only (existing behaviour)
                await apiFetch(`${wizardApiBase()}/documents`, {
                    method: "POST",
                    body: JSON.stringify({ name: doc.name, type: ext.toLowerCase(), category: doc.category, size_kb: doc.size_kb }),
                });
                showToast(`${file.name} uploaded.`, "success");
            }
        } catch(err) {
            console.error("[Upload]", err);
            showToast("Upload failed: " + err.message, "error");
        }
    });
}

function guessCategory(ext) {
    if (ext === "PDF")  return "Blueprints / Permits";
    if (ext === "DOCX") return "Documents";
    if (ext === "XLSX") return "Spreadsheets";
    if (ext === "IFC")  return "BIM Model";
    return "Other";
}

function renderUploadedDocs() {
    const list = document.getElementById("uploadedDocsList");
    if (!list) return;
    list.innerHTML = STATE.uploadedDocs.map(d => `
        <div class="doc-item">
            <span class="doc-type-badge">${d.type}</span>
            <span class="doc-name">${d.name}</span>
            <span class="doc-size">${d.size_kb > 1024 ? (d.size_kb/1024).toFixed(1)+"MB" : d.size_kb+"KB"}</span>
            <button class="doc-remove-btn" onclick="removeUploadedDoc('${d.doc_id}')" title="Remove">✕</button>
        </div>
    `).join("");
}

function removeUploadedDoc(docId) {
    STATE.uploadedDocs = STATE.uploadedDocs.filter(d => d.doc_id !== docId);
    apiFetch(`${wizardApiBase()}/documents/${encodeURIComponent(docId)}`, { method: "DELETE" }).catch(() => {});
    renderUploadedDocs();
}

// ─────────────────────────────────────────────────────────
// STEP 9 — PUBLISH CHECKLIST + PUBLISH  (UC-09.10)
// ─────────────────────────────────────────────────────────
async function loadStep9() {
    const container = document.getElementById("checklistContainer");
    container.innerHTML = `<div class="loading-spinner"><div class="spinner"></div> Building checklist…</div>`;

    // Ensure VR matrix is in STATE — may be empty if user resumed draft without visiting Step 7
    if (STATE.vrMatrix.length === 0 && (STATE.draftId || STATE.editProjectId)) {
        try {
            const res = await apiFetch(`${wizardApiBase()}/vr`);
            STATE.vrMatrix   = res.data;
            STATE.vrDeadline = STATE.vrDeadline || res.compliance_deadline;
        } catch {}
    }

    // Build checklist locally from STATE (mirrors server-side logic)
    const checklist = buildLocalChecklist();
    const hasErrors = checklist.some(c => c.status === "error");
    const projectName = document.getElementById("inp-project_name")?.value || "New Project";
    const teamCount   = STATE.teamMembers.length;
    const ed          = isEditMode();

    const iconMap = { ok: "✅", warning: "⚠️", error: "❌" };

    container.innerHTML = `
        <div class="publish-checklist">
            ${checklist.map(c => `
                <div class="checklist-item ${c.status}">
                    <div class="checklist-icon">${iconMap[c.status]}</div>
                    <div>
                        <div class="checklist-label">${c.label}</div>
                        <div class="checklist-detail">${c.detail}</div>
                    </div>
                    ${c.step ? `<button class="checklist-edit-btn" onclick="goToStep(${c.step})">Edit</button>` : ""}
                </div>
            `).join("")}
        </div>

        <div class="publish-confirm-box">
            <h3>${ed ? "Review & save" : "Ready to Publish?"}</h3>
            <p>${ed
                ? `Review and save <strong>${projectName}</strong>. Changes are applied to the live project.`
                : `You are about to publish <strong>${projectName}</strong> and notify <strong>${teamCount}</strong> team member(s). This action will make the project live.`}</p>
            <button class="btn-publish" ${hasErrors ? "disabled" : ""} onclick="${ed ? "finalizeActiveProject()" : "publishProject()"}">
                ${ed ? "💾 Save all changes" : "🚀 Publish Project"}
            </button>
            ${hasErrors ? `<div style="margin-top:0.75rem;font-size:0.78rem;color:var(--red);">Fix all required sections before continuing.</div>` : ""}
        </div>
    `;
}

function buildLocalChecklist() {
    return [
        {
            label:  "Building Type",
            status: STATE.selectedBuildingType ? "ok" : "error",
            detail: STATE.selectedBuildingType?.name || "Not selected.",
            step:   1,
        },
        {
            label:  "Project Details",
            status: (STATE.projectDetails?.project_name || document.getElementById("inp-project_name")?.value) ? "ok" : "error",
            detail: STATE.projectDetails?.project_name || document.getElementById("inp-project_name")?.value || "Not entered.",
            step:   2,
        },
        {
            label:  "Site Zones",
            status: STATE.zones.length > 0 ? "ok" : "error",
            detail: STATE.zones.length > 0 ? `${STATE.zones.length} zone(s) defined.` : "No zones defined.",
            step:   3,
        },
        {
            label:  "Team Members",
            status: STATE.teamMembers.length > 0 ? (STATE.teamMembers.some(m=>m.role==="Safety Officer") ? "ok" : "warning") : "error",
            detail: STATE.teamMembers.length > 0 ? `${STATE.teamMembers.length} member(s) assigned.` + (!STATE.teamMembers.some(m=>m.role==="Safety Officer") ? " No Safety Officer." : "") : "No team members.",
            step:   4,
        },
        {
            label:  "Resource Plan",
            status: STATE.ganttTasks.length > 0 ? "ok" : "error",
            detail: STATE.ganttTasks.length > 0 ? `${STATE.ganttTasks.length} task(s) scheduled.` : "Not reviewed.",
            step:   5,
        },
        {
            label:  "Safety Protocols",
            status: STATE.safetyRules.length > 0 ? "ok" : "error",
            detail: STATE.safetyRules.length > 0 ? `${STATE.safetyRules.length} protocol(s) confirmed.` : "Not confirmed.",
            step:   6,
        },
        {
            label:  "VR Training Assignments",
            status: STATE.vrMatrix.length > 0 ? "ok" : "error",
            detail: STATE.vrMatrix.length > 0 ? `${STATE.vrMatrix.length} member(s) assigned modules.` : "Not confirmed.",
            step:   7,
        },
        {
            label:  "Project Documents",
            status: STATE.uploadedDocs.length > 0 ? "ok" : "warning",
            detail: STATE.uploadedDocs.length > 0 ? `${STATE.uploadedDocs.length} document(s) uploaded.` : "No documents uploaded (optional).",
            step:   8,
        },
    ];
}

async function finalizeActiveProject() {
    const btn = document.querySelector(".btn-publish");
    if (btn) { btn.disabled = true; btn.textContent = "Saving…"; }

    try {
        const res = await apiFetch(`${wizardApiBase()}/finalize`, { method: "POST" });
        STATE.publishedProjectId = STATE.editProjectId;

        STATE.completedSteps.add(9);
        STATE.completedSteps.add(10);
        goToStep(10);

        document.getElementById("successMsg").textContent = res.message || "All changes saved.";
        document.getElementById("successProjectId").textContent = STATE.editProjectId;
        const st = document.getElementById("successTitle");
        if (st) st.textContent = "Project Updated!";

        showToast("Project updated successfully.", "success");
        document.getElementById("btnNext").style.display = "none";
        document.getElementById("btnBack").style.display = "none";

    } catch (e) {
        showToast("Could not save. Fix required checklist items.", "error");
        if (btn) { btn.disabled = false; btn.textContent = "💾 Save all changes"; }
    }
}

async function publishProject() {
    const btn = document.querySelector(".btn-publish");
    if (btn) { btn.disabled = true; btn.textContent = "Publishing…"; }

    try {
        const res = await apiFetch(`/api/new-project/draft/${STATE.draftId}/publish`, { method: "POST" });
        STATE.publishedProjectId = res.project_id;

        STATE.completedSteps.add(9);
        STATE.completedSteps.add(10);
        goToStep(10);

        document.getElementById("successMsg").textContent =
            `"${res.message}" Notifications sent to: ${res.notifications_sent.join(", ") || "no members"}.`;
        document.getElementById("successProjectId").textContent = res.project_id;
        const st = document.getElementById("successTitle");
        if (st) st.textContent = "Project Published!";

        showToast("Project published successfully! 🎉", "success");
        document.getElementById("btnNext").style.display = "none";
        document.getElementById("btnBack").style.display = "none";

    } catch(e) {
        showToast("Publish failed. Your data has been saved as a draft.", "error");
        if (btn) { btn.disabled = false; btn.textContent = "🚀 Publish Project"; }
    }
}

function goToDashboard() {
    const id = STATE.publishedProjectId || STATE.editProjectId;
    window.location.href = id ? "/dashboard?project=" + encodeURIComponent(id) : "/dashboard";
}

// ─────────────────────────────────────────────────────────
// VALIDATE + SAVE  (called before advancing each step)
// ─────────────────────────────────────────────────────────
async function validateAndSaveStep(step) {
    switch (step) {

        case 1: { // UC-09.2
            if (!STATE.selectedBuildingType) {
                showToast("Please select a building type to continue.", "error");
                return false;
            }
            try {
                await apiFetch(`${wizardApiBase()}/building`, {
                    method: "PUT",
                    body: JSON.stringify({ building_type_id: STATE.selectedBuildingType.id }),
                });
            } catch { showToast("Could not save building type.", "error"); return false; }
            return true;
        }

        case 2: { // UC-09.3
            const fields = ["project_name","client_org","site_address","start_date","end_date","budget","currency","description"];
            const body   = {};
            fields.forEach(f => { const el = document.getElementById(`inp-${f}`); if (el) body[f] = el.value; });

            // ── Client-side pre-validation (instant feedback, no round-trip) ──
            const preErrors = [];
            if (!body.project_name?.trim())  preErrors.push("Project Name is required.");
            if (!body.site_address?.trim())  preErrors.push("Site Address is required.");
            if (!body.start_date)            preErrors.push("Start Date is required.");
            if (!body.end_date)              preErrors.push("Estimated End Date is required.");
            if (body.start_date && body.end_date) {
                const days = Math.round((new Date(body.end_date) - new Date(body.start_date)) / 86400000);
                if (days < 30) preErrors.push(`End Date must be at least 30 days after Start Date (currently ${days} days).`);
            }
            const budgetVal = parseFloat(body.budget);
            if (!body.budget || isNaN(budgetVal) || budgetVal <= 0) preErrors.push("Budget must be a positive number.");

            if (preErrors.length) {
                showToast(preErrors[0], "error");
                return false;
            }

            STATE.projectDetails = { ...body };
            if (STATE.draftId && !isEditMode()) localStorage.setItem("draft_details_" + STATE.draftId, JSON.stringify(body));

            try {
                await apiFetch(`${wizardApiBase()}/details`, {
                    method: "PUT",
                    body: JSON.stringify(body),
                });
            } catch(e) {
                // Surface the server's validation messages if available
                const msgs = e.errors?.length ? e.errors : null;
                const msg  = msgs
                    ? msgs.join(" ")
                    : (e.message_text || `Save failed (HTTP ${e.status || "?"}) — check server logs.`);
                showToast(msg, "error");
                console.error("[Step 2] Save error:", e.status, e.data || e);
                // Highlight fields with errors
                if (msgs) {
                    if (msgs.some(m => m.toLowerCase().includes("name")))     document.getElementById("inp-project_name")?.classList.add("error");
                    if (msgs.some(m => m.toLowerCase().includes("date")))     document.getElementById("inp-start_date")?.classList.add("error");
                    if (msgs.some(m => m.toLowerCase().includes("budget")))   document.getElementById("inp-budget")?.classList.add("error");
                    if (msgs.some(m => m.toLowerCase().includes("address")))  document.getElementById("inp-site_address")?.classList.add("error");
                }
                return false;
            }
            return true;
        }

        case 3: { // UC-09.4
            try {
                await apiFetch(`${wizardApiBase()}/zones`, {
                    method: "PUT",
                    body: JSON.stringify({ zones: STATE.zones }),
                });
            } catch { showToast("Could not save zones.", "error"); return false; }
            return true;
        }

        case 4: { // UC-09.5
            if (STATE.teamMembers.length === 0) {
                showToast("Please add at least one team member.", "error");
                return false;
            }
            saveTeamToLocal();
            try {
                await apiFetch(`${wizardApiBase()}/team`, {
                    method: "PUT",
                    body: JSON.stringify({ members: STATE.teamMembers }),
                });
            } catch { console.warn("Team API save failed — data preserved in localStorage."); }
            return true;
        }

        case 5: { // UC-09.6
            if (STATE.ganttTasks.length === 0) {
                showToast("Gantt has not loaded yet. Please wait and try again.", "error");
                return false;
            }
            try {
                await apiFetch(`${wizardApiBase()}/gantt`, {
                    method: "PUT",
                    body: JSON.stringify({ tasks: STATE.ganttTasks }),
                });
            } catch { showToast("Could not save resource plan.", "error"); return false; }
            return true;
        }

        case 6: { // UC-09.7
            if (STATE.safetyRules.length === 0) {
                showToast("Safety protocols have not loaded. Please wait.", "error");
                return false;
            }
            try {
                await apiFetch(`${wizardApiBase()}/safety`, {
                    method: "PUT",
                    body: JSON.stringify({ rules: STATE.safetyRules }),
                });
            } catch { showToast("Could not save safety protocols.", "error"); return false; }
            return true;
        }

        case 7: { // UC-09.8
            if (STATE.vrMatrix.length === 0) {
                showToast("VR matrix has not loaded. Please wait.", "error");
                return false;
            }
            try {
                await apiFetch(`${wizardApiBase()}/vr`, {
                    method: "PUT",
                    body: JSON.stringify({ matrix: STATE.vrMatrix, compliance_deadline: STATE.vrDeadline }),
                });
            } catch { showToast("Could not save VR assignments.", "error"); return false; }
            return true;
        }

        case 8: { // UC-09.9 — optional
            return true;
        }

        case 9: { // UC-09.10 — publish (new) or finalize (edit)
            if (isEditMode()) {
                await finalizeActiveProject();
                return false;
            }
            await publishProject();
            return false; // don't auto-advance; publish handles it
        }

        default:
            return true;
    }
}

// ─────────────────────────────────────────────────────────
// DATE DURATION CALCULATOR  (UC-09.3 helper)
// ─────────────────────────────────────────────────────────
document.addEventListener("change", e => {
    if (e.target.id === "inp-start_date" || e.target.id === "inp-end_date") {
        const start = document.getElementById("inp-start_date")?.value;
        const end   = document.getElementById("inp-end_date")?.value;
        const disp  = document.getElementById("durationDisplay");
        if (start && end && disp) {
            const days   = Math.round((new Date(end) - new Date(start)) / 86400000);
            const months = (days / 30.4).toFixed(1);
            if (days >= 30) {
                disp.style.display = "inline-block";
                disp.textContent   = `⏱ Duration: ${months} months (${days} days)`;
            } else {
                disp.style.display = "inline-block";
                disp.style.background = "var(--red-dim)";
                disp.style.color = "var(--red)";
                disp.textContent = `⚠ Minimum 30 days required (currently ${days} days)`;
            }
        }
    }
    if (e.target.id === "inp-currency") {
        document.getElementById("currencyPrefix").textContent = e.target.value;
    }
});
// ─────────────────────────────────────────────────────────
// DELETE PROJECT
// ─────────────────────────────────────────────────────────
function confirmDeleteProject(id, name) {
    const modal = document.getElementById("deleteModal");
    document.getElementById("deleteModalMsg").textContent =
        `Are you sure you want to delete "${name}"? This cannot be undone.`;
    modal.classList.remove("hidden");

    document.getElementById("btnConfirmDelete").onclick = async () => {
        closeDeleteModal();
        await deleteProject(id);
    };
}

function closeDeleteModal() {
    document.getElementById("deleteModal").classList.add("hidden");
}

async function deleteProject(id) {
    // Clear all localStorage entries for this draft
    ["draft_lastStep_","draft_buildingType_","draft_team_","draft_gantt_",
     "draft_safety_","draft_vrDeadline_","draft_details_"].forEach(k => {
        localStorage.removeItem(k + id);
    });

    try {
        await apiFetch("/api/new-project/draft/" + id, { method: "DELETE" });
    } catch {
        // Non-fatal — card still removed from UI
    }

    showToast("Project deleted.", "success");
    loadProjectList();
}