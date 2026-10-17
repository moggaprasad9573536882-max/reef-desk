import os
import secrets

import click
from flask import Flask

from . import db


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
        DATABASE=os.environ.get("REEF_DB", os.path.join(app.instance_path, "reef_desk.sqlite3")),
        KNOWLEDGE_FILE=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "knowledge.json"),
        AI_PROVIDER=os.environ.get("AI_PROVIDER", "auto"),            # auto | offline
        AI_BASE_URL=os.environ.get("AI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"),
        AI_MODEL=os.environ.get("AI_MODEL", "gemini-2.5-flash"),
        AI_API_KEY=os.environ.get("GEMINI_API_KEY") or os.environ.get("AI_API_KEY"),  # never committed; set in the host
        AI_TIMEOUT=float(os.environ.get("AI_TIMEOUT", "15")),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
        PERMANENT_SESSION_LIFETIME=8 * 3600,
    )
    if test_config:
        app.config.update(test_config)
    os.makedirs(app.instance_path, exist_ok=True)
    db.init_app(app)

    from . import views
    app.register_blueprint(views.bp)

    @app.after_request
    def security_headers(resp):
        resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; img-src 'self' data:"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        return resp

    @app.cli.command("init-db")
    @click.option("--demo", is_flag=True, help="Load synthetic demo data")
    def init_db_command(demo):
        """Create tables (and optionally synthetic demo data)."""
        from .seed import seed
        conn = db.connect(app.config["DATABASE"])
        db.init_schema(conn)
        if demo:
            password = os.environ.get("REEF_DEMO_PASSWORD") or secrets.token_urlsafe(9)
            seed(conn, app.config["KNOWLEDGE_FILE"], password)
            click.echo(f"Demo users: olivia (owner), sam (staff), kai (guide). Password: {password}")
        click.echo("Database ready.")

    return app
