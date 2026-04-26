"""
config.py
---------
Centralised configuration for the Veritas AI Construction Platform.
Edit values here to configure the application for different environments.
"""

import os

class Config:
    """Base configuration."""
    # IFC /ifc-geometry: IFC_GEOMETRY_CACHE, IFC_GEOMETRY_GZIP, IFC_FAST_GEOMETRY — see api/ifc_route.py.
    SECRET_KEY          = os.environ.get("SECRET_KEY", "veritas-dev-secret-key-2026")
    DEBUG               = False
    TESTING             = False

    # Flask-SocketIO
    SOCKETIO_ASYNC_MODE = "eventlet"

    # How often (seconds) the server pushes a safety-alert refresh to clients
    ALERT_PUSH_INTERVAL = 15

    # Project metadata defaults (would normally come from a database)
    PROJECT_NAME        = "Vocational Center – Phase 1"
    PROJECT_START       = "2026-01-05"
    PROJECT_END_EST     = "2026-08-20"
    PROJECT_BUDGET      = 1_500_000
    PROJECT_SPENT       = 1_200_000


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


# Active config – change "development" → "production" when deploying
config = {
    "development": DevelopmentConfig,
    "production":  ProductionConfig,
    "default":     DevelopmentConfig,
}
