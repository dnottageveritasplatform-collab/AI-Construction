/**
 * Shared project-scoping for Dashboard, Safety Monitor, and other module pages.
 * Persists the selected project ID in localStorage so navigation keeps context.
 *
 * URL conventions:
 *   - Dashboard:     ?project=PRJ-...
 *   - Module pages:  ?project_id=PRJ-...  (e.g. /safety, /resource-plan)
 */
"use strict";

window.VeritasProjectContext = (function () {
    const KEY = "veritas_active_project_id";

    function readPersisted() {
        try {
            return localStorage.getItem(KEY) || "";
        } catch {
            return "";
        }
    }

    function writePersisted(id) {
        try {
            if (id) localStorage.setItem(KEY, id);
            else localStorage.removeItem(KEY);
        } catch {
            /* ignore quota / private mode */
        }
    }

    /** Accepts either ?project= (dashboard) or ?project_id= (module routes). */
    function parseFromUrl(searchParams) {
        const sp = searchParams || new URLSearchParams(window.location.search);
        return (sp.get("project") || sp.get("project_id") || "").trim();
    }

    async function fetchProjectsList(userId) {
        const json = await fetch(`/api/dashboard/projects?user_id=${encodeURIComponent(userId)}`).then(r => r.json());
        let projects = json.data || [];
        try {
            const allRes = await fetch("/api/new-project/projects").then(r => r.json());
            const allProjects = allRes.data || [];
            allProjects.forEach(p => {
                if (p.status === "active" && !projects.find(existing => existing.id === p.id)) {
                    projects.push({
                        id: p.id,
                        name: localStorage.getItem("project_name_" + p.id) || p.name || p.id,
                        completion: p.completion || 0,
                    });
                }
            });
        } catch {
            /* non-fatal */
        }
        return projects;
    }

    /**
     * Pick a valid project id from URL/storage, or the first listed project.
     */
    function resolveActiveId(projects, urlOrStoredId) {
        const id = (urlOrStoredId || "").trim();
        if (!projects.length) return "";
        if (id && projects.some(p => p.id === id)) return id;
        return projects[0].id;
    }

    return {
        KEY,
        readPersisted,
        writePersisted,
        parseFromUrl,
        fetchProjectsList,
        resolveActiveId,
    };
})();
