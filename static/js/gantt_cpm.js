/**
 * gantt_cpm.js — shared working-day CPM + layout for wizard & Resource Plan.
 * Loaded before new_project.js and resource_plan.js.
 */
(function (global) {
    "use strict";

    const GANTT_HOLIDAY_DATES = new Set([]);

    function parseISODateUTC(iso) {
        if (!iso || typeof iso !== "string") return null;
        const p = iso.slice(0, 10).split("-").map(Number);
        const y = p[0], m = p[1], d = p[2];
        if (!y || !m || !d) return null;
        return new Date(Date.UTC(y, m - 1, d));
    }

    function daysBetweenUTC(a, b) {
        return Math.round((b.getTime() - a.getTime()) / 86400000);
    }

    function isoFromUTCDate(dt) {
        return dt.toISOString().slice(0, 10);
    }

    function isWorkingDayISO(iso) {
        const d = parseISODateUTC(iso);
        if (!d) return false;
        const wd = d.getUTCDay();
        if (wd === 0 || wd === 6) return false;
        return !GANTT_HOLIDAY_DATES.has(iso.slice(0, 10));
    }

    function firstWorkingDayOnOrAfterISO(iso) {
        let d = parseISODateUTC(iso);
        if (!d) return iso;
        let s = isoFromUTCDate(d);
        while (!isWorkingDayISO(s)) {
            d.setUTCDate(d.getUTCDate() + 1);
            s = isoFromUTCDate(d);
        }
        return s;
    }

    function addWorkingDaysForwardISO(anchorISO, steps) {
        if (steps <= 0) return anchorISO;
        let d = parseISODateUTC(anchorISO);
        if (!d) return anchorISO;
        let left = steps;
        while (left > 0) {
            d.setUTCDate(d.getUTCDate() + 1);
            if (isWorkingDayISO(isoFromUTCDate(d))) left--;
        }
        return isoFromUTCDate(d);
    }

    function workingDayStartFromOffset(anchorISO, offsetWd) {
        if (offsetWd <= 0) return anchorISO;
        return addWorkingDaysForwardISO(anchorISO, offsetWd);
    }

    function taskLastDayFromStartAndWdDuration(startISO, durWd) {
        const d = Math.max(1, Math.round(Number(durWd)) || 1);
        if (d <= 1) return firstWorkingDayOnOrAfterISO(startISO);
        return addWorkingDaysForwardISO(firstWorkingDayOnOrAfterISO(startISO), d - 1);
    }

    function minStartWForUserCalendarDate(anchorISO, userDateISO) {
        const u = firstWorkingDayOnOrAfterISO(userDateISO);
        let w = 0;
        for (;;) {
            const cand = workingDayStartFromOffset(anchorISO, w);
            if (cand >= u) return w;
            w++;
            if (w > 5000) return w;
        }
    }

    function inferResourcePoolJs(name) {
        const n = (name || "").toLowerCase();
        if (n.includes("mep")) return "crew_mep";
        if (n.includes("roof")) return "crew_roof";
        if (n.includes("foundation") || n.includes("concrete") || n.includes("pour") || n.includes("slab")) return "crew_concrete";
        if (n.includes("excavat") || n.includes("site prep") || n.includes("piling")) return "crew_earth";
        if (n.includes("frame") || n.includes("steel") || n.includes("structural")) return "crew_structure";
        if (n.includes("facade") || n.includes("cladding")) return "crew_envelope";
        if (n.includes("drywall") || n.includes("insulation") || n.includes("fit-out") || n.includes("fit out") || n.includes("interior")) return "crew_interior";
        if (n.includes("inspect") || n.includes("handover") || n.includes("commission")) return "crew_closeout";
        return "general";
    }

    function taskResourcePool(t) {
        const p = t.resource_pool;
        if (p != null && String(p).trim() !== "") return String(p).trim();
        return inferResourcePoolJs(t.name);
    }

    function depLagWd(t, depArrayIndex) {
        const arr = t.dep_lag_wd;
        if (!Array.isArray(arr)) return 0;
        return Math.max(0, parseInt(arr[depArrayIndex], 10) || 0);
    }

    function getSpanFromDates(startS, endS) {
        if (!startS || !endS) return { totalDays: 1, startISO: null, endISO: null };
        const start = parseISODateUTC(startS);
        const end = parseISODateUTC(endS);
        if (!start || !end) return { totalDays: 1, startISO: null, endISO: null };
        const total = Math.max(1, daysBetweenUTC(start, end));
        return { totalDays: total, startISO: startS, endISO: endS };
    }

    /**
     * Resolve predecessor to row index.
     * Never use TASK-NNN numeric suffix as array index — merged lists / deletions break that mapping.
     */
    function depIdToIndex(tasks, depId) {
        const raw = String(depId || "").trim();
        if (!raw) return -1;
        let idx = tasks.findIndex(t => String(t.id) === raw);
        if (idx >= 0) return idx;
        const m = raw.match(/^TASK-(\d+)$/i);
        if (m) {
            const num = parseInt(m[1], 10);
            const candidates = new Set([
                raw.toUpperCase(),
                `TASK-${String(num).padStart(3, "0")}`.toUpperCase(),
                `TASK-${num}`.toUpperCase(),
            ]);
            idx = tasks.findIndex(t => candidates.has(String(t.id || "").toUpperCase()));
            if (idx >= 0) return idx;
        }
        return -1;
    }

    /** Kahn topological order on FS edges (pred → task). Null if cycle or unresolved deps. */
    function ganttTopologicalOrder(tasks) {
        const n = tasks.length;
        const indegree = new Array(n).fill(0);
        const adj = Array.from({ length: n }, () => []);
        for (let j = 0; j < n; j++) {
            const deps = tasks[j].deps;
            if (!Array.isArray(deps)) continue;
            for (const id of deps) {
                const p = depIdToIndex(tasks, id);
                if (p >= 0 && p < n && p !== j) {
                    adj[p].push(j);
                    indegree[j]++;
                }
            }
        }
        const q = [];
        for (let i = 0; i < n; i++) {
            if (indegree[i] === 0) q.push(i);
        }
        const order = [];
        while (q.length) {
            const u = q.shift();
            order.push(u);
            for (const v of adj[u]) {
                indegree[v]--;
                if (indegree[v] === 0) q.push(v);
            }
        }
        return order.length === n ? order : null;
    }

    function taskIdAtIndex(tasks, k) {
        if (!tasks[k]) return "";
        return tasks[k].id || `TASK-${String(k + 1).padStart(3, "0")}`;
    }

    function ganttGraphHasCycle(tasks) {
        const n = tasks.length;
        const adj = Array.from({ length: n }, () => []);
        for (let j = 0; j < n; j++) {
            const deps = tasks[j].deps;
            if (!Array.isArray(deps)) continue;
            for (const id of deps) {
                const p = depIdToIndex(tasks, id);
                if (p >= 0 && p < n && p !== j) adj[p].push(j);
            }
        }
        const state = new Array(n).fill(0);
        function dfs(u) {
            if (state[u] === 1) return true;
            if (state[u] === 2) return false;
            state[u] = 1;
            for (const v of adj[u]) {
                if (dfs(v)) return true;
            }
            state[u] = 2;
            return false;
        }
        for (let i = 0; i < n; i++) {
            if (state[i] === 0 && dfs(i)) return true;
        }
        return false;
    }

    function recalculateGanttCPM(tasks, span, opts = {}) {
        const startISO = span.startISO;
        const endISO = span.endISO;
        if (!startISO || !tasks.length) return { overriddenStart: false, anyPastProjectEnd: false };

        const anchor = firstWorkingDayOnOrAfterISO(startISO);
        const editedIndex = Number.isInteger(opts.editedIndex) ? opts.editedIndex : -1;
        const minIso = opts.editedMinStartISO;
        let editedMinStartW = null;
        if (editedIndex >= 0 && minIso) {
            editedMinStartW = minStartWForUserCalendarDate(anchor, minIso);
        }

        const endExclusiveW = [];
        const poolLastEnd = Object.create(null);
        let overriddenStart = false;

        const topo = ganttTopologicalOrder(tasks);
        const processOrder = topo || tasks.map((_, idx) => idx);

        for (let ord = 0; ord < processOrder.length; ord++) {
            const i = processOrder[ord];
            const t = tasks[i];
            const depIds = Array.isArray(t.deps) ? t.deps : [];
            let earliestW = 0;
            depIds.forEach((d, di) => {
                const j = depIdToIndex(tasks, d);
                const lag = depLagWd(t, di);
                if (j >= 0 && j < tasks.length && endExclusiveW[j] != null) {
                    earliestW = Math.max(earliestW, endExclusiveW[j] + lag);
                }
            });

            const dur = Math.max(1, Math.round(Number(t.duration)) || 1);
            t.duration = dur;
            t.duration_wd = dur;

            const pool = taskResourcePool(t);
            let resFloor = 0;
            if (pool !== "general") {
                resFloor = poolLastEnd[pool] || 0;
            }

            const depResFloor = Math.max(earliestW, resFloor);
            let startW;
            if (i === editedIndex && editedMinStartW != null) {
                startW = Math.max(depResFloor, editedMinStartW);
                if (editedMinStartW < depResFloor) overriddenStart = true;
            } else {
                startW = depResFloor;
            }

            const endEx = startW + dur;
            endExclusiveW[i] = endEx;
            if (pool !== "general") {
                poolLastEnd[pool] = endEx;
            }

            const sDate = workingDayStartFromOffset(anchor, startW);
            t.start_date = sDate;
            t.end_date = taskLastDayFromStartAndWdDuration(sDate, dur);
            syncGanttTaskLayout(t, span);
        }

        const projEnd = endISO ? parseISODateUTC(endISO) : null;
        let anyPastProjectEnd = false;
        if (projEnd) {
            for (let k = 0; k < tasks.length; k++) {
                const e = parseISODateUTC(tasks[k].end_date);
                if (e && e > projEnd) {
                    anyPastProjectEnd = true;
                    break;
                }
            }
        }

        return { overriddenStart, anyPastProjectEnd };
    }

    function syncGanttTaskLayout(task, span) {
        const totalDays = span.totalDays;
        const startISO = span.startISO;
        if (!startISO || !task.start_date) return;
        const projStart = parseISODateUTC(startISO);
        const tStart = parseISODateUTC(task.start_date);
        if (!projStart || !tStart) return;
        let offset = daysBetweenUTC(projStart, tStart);
        if (offset < 0) offset = 0;
        const durWd = Math.max(1, Math.round(Number(task.duration)) || 1);
        task.duration = durWd;
        if (!task.end_date) {
            task.end_date = taskLastDayFromStartAndWdDuration(task.start_date, durWd);
        }
        const tEnd = parseISODateUTC(task.end_date);
        const calSpan = tEnd ? Math.max(1, daysBetweenUTC(tStart, tEnd) + 1) : durWd;
        task.start_offset_pct = Math.min(100, Math.round((offset / totalDays) * 1000) / 10);
        task.width_pct = Math.min(100, Math.max(0.3, Math.round((calSpan / totalDays) * 1000) / 10));
    }

    global.VeritasGantt = {
        parseISODateUTC,
        daysBetweenUTC,
        firstWorkingDayOnOrAfterISO,
        addWorkingDaysForwardISO,
        workingDayStartFromOffset,
        taskLastDayFromStartAndWdDuration,
        minStartWForUserCalendarDate,
        inferResourcePoolJs,
        taskResourcePool,
        depLagWd,
        getSpanFromDates,
        depIdToIndex,
        taskIdAtIndex,
        ganttGraphHasCycle,
        recalculateGanttCPM,
        syncGanttTaskLayout,
    };
})(typeof window !== "undefined" ? window : globalThis);
