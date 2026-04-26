# Veritas AI Construction Platform
**Creator:** Dominic R. Nottage, PMP · CSM  
**Client:** BTVI – Vocational School Construction Department  
**Version:** 1.0.0 | February 2026

---

## Directory Structure

```
veritas-ai-platform/
│
├── app.py                    # ← Main entry point (run this to start the server)
├── config.py                 # Environment & application configuration
├── requirements.txt          # Python package dependencies
├── README.md                 # This file
│
├── api/                      # REST API blueprints (one file per module)
│   ├── __init__.py
│   ├── dashboard.py          # GET /api/dashboard/* endpoints
│   ├── safety.py             # GET /api/safety/*  endpoints
│   ├── resources.py          # GET /api/resources/* endpoints
│   ├── project.py            # GET /api/project/*  endpoints
│   └── vr_training.py        # GET /api/vr/*       endpoints
│
├── data/                     # Data layer (mock data / replace with real DB)
│   ├── __init__.py
│   └── mock_data.py          # In-memory data store & helper functions
│
├── static/                   # Static assets served by Flask
│   ├── css/                  # (reserved for future global stylesheets)
│   ├── js/                   # (reserved for shared JS utilities)
│   └── assets/               # Images, icons, uploads
│       ├── 3d_model.png
│       └── passportphotodominicnottage.jpg
│
└── templates/                # Jinja2 HTML templates rendered by Flask
    ├── dashboard.html         # ← Main Dashboard (fully wired to API; project summary, docs, team)
    ├── new_project_wizard.html # New / edit project wizard (UC-09)
    ├── resourciist.html       # Resource List (asset inventory)
    ├── resource_plan.html     # Gantt / resource planning view
    ├── safety_monitor.html    # Real-time safety camera & alert log
    └── vr_training.html       # VR Training Hub
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Server health check |
| GET | `/api/dashboard/summary` | KPI summary tiles |
| GET | `/api/dashboard/progress` | Project progress graph data |
| GET | `/api/dashboard/alerts?severity=` | Safety alerts list |
| GET | `/api/dashboard/tasks?status=` | Upcoming tasks |
| GET | `/api/dashboard/vr-training` | VR training status |
| GET | `/api/dashboard/3d-model?filter=` | 3D model phase data |
| GET | `/api/dashboard/user` | Current user profile |
| GET | `/api/safety/alerts` | Full safety alert list |
| POST | `/api/safety/alerts/<id>/acknowledge` | Acknowledge an alert |
| GET | `/api/safety/zones` | Camera zone status |
| GET | `/api/resources/assets?category=&status=&q=` | Asset inventory |
| GET | `/api/resources/summary` | Asset KPI summary |
| GET | `/api/project/details` | Project metadata |
| GET | `/api/project/team` | Team roster |
| GET | `/api/project/documents` | Project documents |
| GET | `/api/project/bim` | BIM model metadata |
| GET | `/api/vr/modules?status=` | VR training modules |
| POST | `/api/vr/modules/<id>/launch` | Launch a VR module |

---

## Installation & Setup

### Step 1 — Install Python 3.11+

#### Windows
1. Go to https://www.python.org/downloads/
2. Download **Python 3.11** or newer (check "Add Python to PATH" during install)
3. Open **Command Prompt** and verify:
   ```
   python --version
   ```

#### macOS
```bash
# Option A – Homebrew (recommended)
brew install python@3.11

# Option B – Download installer from python.org
```

#### Linux (Ubuntu / Debian)
```bash
sudo apt update
sudo apt install python3.11 python3.11-venv python3-pip -y
```

---

### Step 2 — Navigate to the project directory

```bash
cd path/to/veritas-ai-platform
```

---

### Step 3 — Create a virtual environment

A virtual environment keeps project dependencies isolated from
your system Python installation.

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
```

You should see `(venv)` in your terminal prompt.

---

### Step 4 — Install dependencies

```bash
pip install -r requirements.txt
```

This installs:
- **Flask 3.0** – web framework
- **Flask-CORS** – cross-origin resource sharing
- **Flask-SocketIO** – WebSocket support for real-time alerts
- **eventlet** – async networking for SocketIO
- **Werkzeug** – WSGI utilities

---

### Step 5 — Copy your project assets

Place the following files inside `static/assets/`:

| File | Description |
|------|-------------|
| `3d_model.png` | 3D building model image |
| `passportphotodominicnottage.jpg` | User avatar |

---

### Step 6 — Run the application

```bash
python app.py
```

The terminal will display:
```
============================================================
  Veritas AI Construction Platform
  http://localhost:5000
============================================================
```

Open your browser and go to: **http://localhost:5000**

---

## Page Routes

| URL | Page |
|-----|------|
| `http://localhost:5000/` | Dashboard (Home) |
| `http://localhost:5000/dashboard` | Dashboard |
| `http://localhost:5000/resourciist` | Resource List |
| `http://localhost:5000/resource-plan` | Resource Plan (Gantt) |
| `http://localhost:5000/new-project` | New Project wizard |
| `http://localhost:5000/edit-project?project=PRJ-...` | Edit active project (redirects into wizard) |
| `http://localhost:5000/safety` | Safety Monitor |
| `http://localhost:5000/vr-training` | VR Training Hub |

---

## Real-Time Features

The dashboard uses **WebSockets** (via Socket.IO) for live data push:

- Safety alerts auto-refresh every **15 seconds**
- The server pushes updates to ALL connected clients simultaneously
- If WebSocket is unavailable, the client falls back to **HTTP polling** (every 30 s)

---

## Configuration

Edit `config.py` to change:

| Setting | Default | Description |
|---------|---------|-------------|
| `SECRET_KEY` | `veritas-dev-secret-key-2026` | Change before production deployment |
| `DEBUG` | `True` (dev) | Disable in production |
| `ALERT_PUSH_INTERVAL` | `15` seconds | WebSocket push frequency |
| `PROJECT_BUDGET` | `1,500,000` | Project budget (USD) |

---

## Connecting Real Data Sources

Replace `data/mock_data.py` with live integrations:

| Data Source | Integration Point | Technology |
|-------------|------------------|------------|
| BIM / 3D Model | `api/project.py` → `/api/project/bim` | Autodesk Construction Cloud API |
| Safety Alerts | `api/safety.py` → MQTT broker | AWS IoT Core / Mosquitto |
| Task Scheduling | `api/dashboard.py` → `/api/dashboard/tasks` | MS Project / Primavera REST |
| VR Training | `api/vr_training.py` | Moodle LMS REST API |
| Asset Inventory | `api/resources.py` | ERP system (e.g., SAP) |

---

## Troubleshooting

| Issue | Solution |
|-------|---------|
| `ModuleNotFoundError: No module named 'flask'` | Run `pip install -r requirements.txt` inside your activated `venv` |
| `Address already in use` | Another process is using port 5000. Run `python app.py` after closing other servers, or change `port=5000` in `app.py` |
| Images not showing | Ensure `3d_model.png` and avatar image are inside `static/assets/` |
| WebSocket not connecting | Check browser console; make sure `eventlet` is installed |

---

*Veritas AI Construction Platform — Vocational School Construction Dept.*
