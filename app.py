"""
app.py
------
Main entry point for the Veritas AI Construction Platform (Flask).

Run with:
    python app.py

The server will start on http://localhost:5000

Dependencies (all included in requirements.txt):
    pip install flask flask-cors flask-socketio eventlet

If flask-cors / flask-socketio are not yet installed the app will still
run in a reduced mode – CORS headers are added manually and real-time
updates fall back to the client-side 30-second HTTP polling.
"""

import os
import json
import queue
import threading
import time
from datetime import datetime
from functools import wraps

from urllib.parse import urlencode

from flask import (Flask, render_template, jsonify,
                   Response, stream_with_context, request, redirect,
                   send_from_directory)

from config          import config
from api.dashboard   import dashboard_bp
from api.safety      import safety_bp
from api.resources   import resources_bp
from api.project     import project_bp
from api.vr_training import vr_bp
from api.new_project import new_project_bp          # UC-09
from api.ifc_route   import ifc_bp                     # IFC upload


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app(env: str = "development") -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config.from_object(config[env])

    # ---- Manual CORS (works without flask-cors package) ------------------
    @app.after_request
    def add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"]  = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
        return response

    # Register API blueprints
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(safety_bp)
    app.register_blueprint(resources_bp)
    app.register_blueprint(project_bp)
    app.register_blueprint(vr_bp)
    app.register_blueprint(new_project_bp)          # UC-09
    app.register_blueprint(ifc_bp)                     # IFC upload

    return app


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------
app = create_app(os.environ.get("FLASK_ENV", "development"))

# ---------------------------------------------------------------------------
# Server-Sent Events (SSE) — real-time push without WebSocket dependency
# Each connected client gets its own queue; the broadcaster thread puts
# events into all queues every ALERT_PUSH_INTERVAL seconds.
# ---------------------------------------------------------------------------
_sse_clients: list[queue.Queue] = []
_sse_lock = threading.Lock()


def _sse_broadcast(data: dict):
    """Push a JSON payload to every connected SSE client."""
    msg = f"data: {json.dumps(data)}\n\n"
    with _sse_lock:
        dead = []
        for q in _sse_clients:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _sse_clients.remove(q)


def _broadcast_loop():
    """Background thread: signal clients to refresh every N seconds.
    
    NOTE: We deliberately do NOT push alert data in the SSE payload.
    The old code pushed mock SAFETY_ALERTS here which overwrote real
    project alerts in the dashboard widget and caused wrong IDs to be
    sent to the Safety Monitor deep-link (UC-03 bug).
    Clients call /api/dashboard/alerts independently to get real data.
    """
    interval = app.config.get("ALERT_PUSH_INTERVAL", 15)
    with app.app_context():
        while True:
            time.sleep(interval)
            _sse_broadcast({"type": "dashboard_update"})


# Start broadcaster daemon
threading.Thread(target=_broadcast_loop, daemon=True).start()


# ---------------------------------------------------------------------------
# SSE endpoint — clients subscribe here
# ---------------------------------------------------------------------------
@app.route("/api/events")
def sse_stream():
    """
    Server-Sent Events endpoint.
    Usage in browser JS:
        const es = new EventSource('/api/events');
        es.onmessage = e => console.log(JSON.parse(e.data));
    """
    q: queue.Queue = queue.Queue(maxsize=20)
    with _sse_lock:
        _sse_clients.append(q)

    # Send immediate snapshot — signal only, no data payload.
    # Alert data is fetched by clients via /api/dashboard/alerts directly.
    initial = json.dumps({"type": "dashboard_update"})

    @stream_with_context
    def generate():
        yield f"data: {initial}\n\n"
        while True:
            try:
                msg = q.get(timeout=25)   # heartbeat timeout
                yield msg
            except queue.Empty:
                yield ": heartbeat\n\n"  # keep connection alive

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering":"no",
            "Connection":       "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# Authentication routes
# ---------------------------------------------------------------------------

@app.route("/login")
def login_page():
    return render_template("login.html")


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    """
    Stub login endpoint.
    In production, validate credentials against a real user store or LDAP.
    For the prototype, any valid-format email + non-empty password succeeds.
    """
    body     = request.get_json(silent=True) or {}
    email    = (body.get("email") or "").strip()
    password = body.get("password") or ""

    if not email or not password:
        return jsonify({"status": "error", "message": "Email and password are required."}), 400
    if "@" not in email:
        return jsonify({"status": "error", "message": "Invalid email address."}), 401

    # TODO: replace with real credential verification
    return jsonify({
        "status":  "ok",
        "message": "Login successful.",
        "user": {
            "id":    "usr-001",
            "name":  "D. Nottage",
            "role":  "Lead Instructor",
            "email": email,
        },
    })


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    """
    Logout endpoint. Clears the server-side session (if any) and
    returns a redirect hint for the client.
    Add  session.clear()  here when Flask-Login is integrated.
    """
    return jsonify({
        "status":      "ok",
        "message":     "You have been signed out.",
        "redirect_to": "/login?signedout=1",
    })


# ---------------------------------------------------------------------------
# Page routes  (serve HTML templates)
# ---------------------------------------------------------------------------

@app.route("/favicon.ico")
def favicon():
    """Browsers request /favicon.ico by default; serve icon to avoid 404 in devtools."""
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "favicon.svg",
        mimetype="image/svg+xml",
    )


@app.route("/")
@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.route("/resourciist")
def resourciist():
    return render_template("resourciist.html")


@app.route("/resource-plan")
def resource_plan():
    return render_template("resource_plan.html")


@app.route("/safety")
def safety():
    return render_template("safety_monitor.html")


@app.route("/vr-training")
def vr_training():
    return render_template("vr_training.html")


@app.route("/edit-project")
def edit_project_redirect():
    """Alias for the active-project wizard: /new-project?edit=<id>."""
    pid = (request.args.get("project") or request.args.get("edit") or "").strip()
    if not pid or not pid.startswith("PRJ-"):
        return redirect("/new-project")
    return redirect("/new-project?" + urlencode({"edit": pid}))


@app.route("/new-project")                           # UC-09
def new_project():
    return render_template("new_project_wizard.html")


# ---------------------------------------------------------------------------
# Health-check endpoint
# ---------------------------------------------------------------------------
@app.route("/api/health")
def health():
    return jsonify({
        "status":    "ok",
        "platform":  "Veritas AI Construction Platform",
        "version":   "1.0.0",
        "timestamp": datetime.now().isoformat(),
        "sse_clients": len(_sse_clients),
    })


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("  Veritas AI Construction Platform")
    print("  http://localhost:5000")
    print("=" * 60)
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=app.config["DEBUG"],
        threaded=True,           # Required for SSE concurrent connections
    )