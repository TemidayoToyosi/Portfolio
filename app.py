import re
import os
import secrets
import sqlite3
import smtplib
from datetime import datetime
from contextlib import contextmanager
from email.message import EmailMessage
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar
from urllib.parse import urlparse

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from jinja2 import ChoiceLoader, FileSystemLoader
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "instance" / "portfolio.sqlite3"
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
F = TypeVar("F", bound=Callable[..., Any])


def load_local_env() -> None:
    env_file = BASE_DIR / ".env"
    if not env_file.is_file():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        if not separator or not name.strip():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        key = name.strip()
        if not os.environ.get(key):
            os.environ[key] = value


load_local_env()


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=str(DATABASE_PATH),
        MAX_CONTENT_LENGTH=6 * 1024 * 1024,
        MAX_PROJECT_IMAGE_SIZE=5 * 1024 * 1024,
        MAX_RESUME_SIZE=5 * 1024 * 1024,
        UPLOAD_FOLDER=str(BASE_DIR / "static" / "uploads"),
        RESUME_PATH=str(BASE_DIR / "instance" / "resume.pdf"),
        SECRET_KEY=os.environ.get("SESSION_SECRET", secrets.token_hex(32)),
        ADMIN_PASSWORD=os.environ.get("ADMIN_PASSWORD", ""),
        CONTACT_EMAIL="temidayoeze98@gmail.com",
        SMTP_HOST=os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        SMTP_PORT=int(os.environ.get("SMTP_PORT", "587")),
        SMTP_USERNAME=os.environ.get("SMTP_USERNAME", ""),
        SMTP_PASSWORD=os.environ.get("SMTP_PASSWORD", ""),
        SMTP_FROM=os.environ.get("SMTP_FROM", ""),
        SMTP_USE_TLS=os.environ.get("SMTP_USE_TLS", "true").lower() == "true",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true",
    )
    if test_config:
        app.config.update(test_config)
    app.jinja_loader = ChoiceLoader([app.jinja_loader, FileSystemLoader(str(BASE_DIR))])

    @contextmanager
    def get_db() -> Iterator[sqlite3.Connection]:
        database_path = Path(app.config["DATABASE"])
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def admin_required(view: F) -> F:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any):
            if not session.get("admin_authenticated"):
                return redirect(url_for("admin_login"))
            return view(*args, **kwargs)

        return wrapped  # type: ignore[return-value]

    def validate_csrf() -> None:
        submitted_token = request.form.get("csrf_token", "")
        session_token = session.get("csrf_token", "")
        if not session_token or not secrets.compare_digest(submitted_token, session_token):
            abort(400)

    def send_contact_email(name: str, email: str, message: str) -> None:
        username = app.config["SMTP_USERNAME"]
        password = "".join(app.config["SMTP_PASSWORD"].split())
        sender = app.config["SMTP_FROM"] or username
        if not username or not password or not sender:
            raise RuntimeError("SMTP credentials are not configured.")

        email_message = EmailMessage()
        email_message["Subject"] = f"Portfolio message from {name}"
        email_message["From"] = sender
        email_message["To"] = app.config["CONTACT_EMAIL"]
        email_message["Reply-To"] = email
        email_message.set_content(f"From: {name} <{email}>\n\n{message}")

        with smtplib.SMTP(app.config["SMTP_HOST"], app.config["SMTP_PORT"], timeout=15) as smtp:
            if app.config["SMTP_USE_TLS"]:
                smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(email_message)

    with app.app_context():
        with get_db() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    description TEXT NOT NULL,
                    link TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS site_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            project_count = connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            if project_count == 0:
                connection.executemany(
                    "INSERT INTO projects (title, category, description) VALUES (?, ?, ?)",
                    [
                        (
                            "Computer Science Final Year Web System",
                            "Lagos State University, 2026",
                            "A web-based application built with HTML, CSS, JavaScript, Python and Flask. It has a responsive interface, supported by full system documentation and research.",
                        ),
                        (
                            "Responsive Front-End Web Applications",
                            "HTML · CSS · JavaScript",
                            "Modular web components translated from design specs, with attention to UI layout, cross-device responsiveness and accessibility.",
                        ),
                        (
                            "Technical & Business Research Reports",
                            "Research and documentation",
                            "Structured research, analytical reporting and technical documentation prepared for complex web systems and operational needs.",
                        ),
                    ],
                )

    @app.get("/")
    def home():
        with get_db() as connection:
            projects = connection.execute(
                "SELECT id, title, category, description, link FROM projects ORDER BY id DESC"
            ).fetchall()
            setting = connection.execute(
                "SELECT value FROM site_settings WHERE key = 'profile_image'"
            ).fetchone()
        return render_template(
            "index.html",
            projects=projects,
            profile_image=setting["value"] if setting else "",
            resume_exists=Path(app.config["RESUME_PATH"]).is_file(),
        )

    @app.get("/resume")
    def download_resume():
        resume_path = Path(app.config["RESUME_PATH"])
        if not resume_path.is_file():
            abort(404)
        return send_file(resume_path, mimetype="application/pdf", as_attachment=True, download_name="Temidayo_Toyosi_Ezekiel_Resume.pdf")

    @app.post("/api/contact")
    def create_contact():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error="Send the contact form as JSON."), 400

        name = payload.get("name", "")
        email = payload.get("email", "")
        message = payload.get("message", "")
        if not all(isinstance(value, str) for value in (name, email, message)):
            return jsonify(error="Name, email, and message must be text."), 400

        name, email, message = name.strip(), email.strip(), message.strip()
        if not name or len(name) > 120:
            return jsonify(error="Enter a name of 1 to 120 characters."), 400
        if len(email) > 254 or not EMAIL_PATTERN.fullmatch(email):
            return jsonify(error="Enter a valid email address."), 400
        if not message or len(message) > 5000:
            return jsonify(error="Enter a message of 1 to 5000 characters."), 400

        with get_db() as connection:
            connection.execute(
                "INSERT INTO messages (name, email, message) VALUES (?, ?, ?)",
                (name, email, message),
            )

        try:
            send_contact_email(name, email, message)
        except (OSError, smtplib.SMTPException, RuntimeError):
            app.logger.exception("Could not deliver portfolio contact email")
            return jsonify(error="Your message was saved, but email delivery failed. Please try again later."), 502

        return jsonify(message="Thanks, your message has been received."), 201

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if session.get("admin_authenticated"):
            return redirect(url_for("admin_inbox"))

        if request.method == "POST":
            validate_csrf()
            configured_password = app.config["ADMIN_PASSWORD"]
            submitted_password = request.form.get("password", "")
            if configured_password and secrets.compare_digest(submitted_password, configured_password):
                session.clear()
                session["admin_authenticated"] = True
                session["csrf_token"] = secrets.token_urlsafe(32)
                return redirect(url_for("admin_inbox"))
            flash("The password was incorrect or admin access is not configured.", "error")

        session.setdefault("csrf_token", secrets.token_urlsafe(32))
        return render_template("admin_login.html")

    @app.get("/admin")
    def admin_home():
        if not session.get("admin_authenticated"):
            return redirect(url_for("admin_login"))
        return redirect(url_for("admin_inbox"))

    @app.get("/admin/inbox")
    @admin_required
    def admin_inbox():
        page = request.args.get("page", 1, type=int)
        page = max(page or 1, 1)
        page_size = 25
        with get_db() as connection:
            total = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            messages = connection.execute(
                "SELECT id, name, email, message, created_at FROM messages "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (page_size, (page - 1) * page_size),
            ).fetchall()
        return render_template(
            "admin_inbox.html",
            messages=messages,
            page=page,
            page_count=max((total + page_size - 1) // page_size, 1),
            total=total,
            csrf_token=session["csrf_token"],
        )

    @app.route("/admin/projects", methods=["GET", "POST"])
    @admin_required
    def admin_projects():
        if request.method == "POST":
            validate_csrf()
            title = request.form.get("title", "").strip()
            category = request.form.get("category", "").strip()
            description = request.form.get("description", "").strip()
            link = request.form.get("link", "").strip()
            if not title or len(title) > 120 or not category or len(category) > 120:
                flash("Enter a project title and category, each no longer than 120 characters.", "error")
            elif not description or len(description) > 2000:
                flash("Enter a description of 1 to 2000 characters.", "error")
            elif link and (urlparse(link).scheme not in {"http", "https"} or not urlparse(link).netloc):
                flash("Project link must be a valid http or https URL.", "error")
            else:
                with get_db() as connection:
                    connection.execute(
                        "INSERT INTO projects (title, category, description, link) VALUES (?, ?, ?, ?)",
                        (title, category, description, link),
                    )
                flash("Project added to the portfolio.", "success")
                return redirect(url_for("admin_projects"))

        with get_db() as connection:
            projects = connection.execute(
                "SELECT id, title, category, description, link FROM projects ORDER BY id DESC"
            ).fetchall()
            setting = connection.execute(
                "SELECT value FROM site_settings WHERE key = 'profile_image'"
            ).fetchone()
        return render_template(
            "admin_projects.html",
            projects=projects,
            profile_image=setting["value"] if setting else "",
            resume_exists=Path(app.config["RESUME_PATH"]).is_file(),
            csrf_token=session["csrf_token"],
        )

    @app.post("/admin/projects/<int:project_id>/edit")
    @admin_required
    def edit_project(project_id: int):
        validate_csrf()
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "").strip()
        description = request.form.get("description", "").strip()
        link = request.form.get("link", "").strip()

        if not title or len(title) > 120 or not category or len(category) > 120:
            flash("Enter a project title and category, each no longer than 120 characters.", "error")
        elif not description or len(description) > 2000:
            flash("Enter a description of 1 to 2000 characters.", "error")
        elif link and (urlparse(link).scheme not in {"http", "https"} or not urlparse(link).netloc):
            flash("Project link must be a valid http or https URL.", "error")
        else:
            with get_db() as connection:
                result = connection.execute(
                    "UPDATE projects SET title = ?, category = ?, description = ?, link = ? WHERE id = ?",
                    (title, category, description, link, project_id),
                )
            if result.rowcount:
                flash("Project updated.", "success")
            else:
                flash("Project not found.", "error")
                abort(404)
            return redirect(url_for("admin_projects"))

        return redirect(url_for("admin_projects"))

    @app.post("/admin/resume")
    @admin_required
    def upload_resume():
        validate_csrf()
        resume = request.files.get("resume")
        if not resume or not resume.filename:
            flash("Choose a resume PDF first.", "error")
            return redirect(url_for("admin_projects"))
        if Path(resume.filename).suffix.lower() != ".pdf":
            flash("Resume must be a PDF file.", "error")
            return redirect(url_for("admin_projects"))

        pdf_bytes = resume.stream.read(app.config["MAX_RESUME_SIZE"] + 1)
        if len(pdf_bytes) > app.config["MAX_RESUME_SIZE"]:
            flash("Resume must be 5 MB or smaller.", "error")
            return redirect(url_for("admin_projects"))
        if not pdf_bytes.startswith(b"%PDF-") or b"%%EOF" not in pdf_bytes[-1024:]:
            flash("That file does not appear to be a valid PDF.", "error")
            return redirect(url_for("admin_projects"))

        resume_path = Path(app.config["RESUME_PATH"])
        resume_path.parent.mkdir(parents=True, exist_ok=True)
        resume_path.write_bytes(pdf_bytes)
        flash("Resume uploaded and download link enabled.", "success")
        return redirect(url_for("admin_projects"))

    @app.post("/admin/resume/delete")
    @admin_required
    def delete_resume():
        validate_csrf()
        resume_path = Path(app.config["RESUME_PATH"])
        if resume_path.is_file():
            resume_path.unlink()
            flash("Resume removed and public download link disabled.", "success")
        else:
            flash("There is no resume to remove.", "error")
        return redirect(url_for("admin_projects"))

    @app.post("/admin/projects/<int:project_id>/delete")
    @admin_required
    def delete_project(project_id: int):
        validate_csrf()
        with get_db() as connection:
            connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        flash("Project removed.", "success")
        return redirect(url_for("admin_projects"))

    @app.post("/admin/profile-image")
    @admin_required
    def upload_profile_image():
        validate_csrf()
        image = request.files.get("profile_image")
        if not image or not image.filename:
            flash("Choose an image file first.", "error")
            return redirect(url_for("admin_projects"))
        extension = Path(image.filename).suffix.lower()
        allowed_extensions = {".jpg", ".jpeg", ".png", ".webp"}
        if extension not in allowed_extensions:
            flash("Use a JPG, PNG, or WebP image.", "error")
            return redirect(url_for("admin_projects"))

        image_bytes = image.stream.read(app.config["MAX_PROJECT_IMAGE_SIZE"] + 1)
        if len(image_bytes) > app.config["MAX_PROJECT_IMAGE_SIZE"]:
            flash("Profile images must be 5 MB or smaller.", "error")
            return redirect(url_for("admin_projects"))
        image.stream.seek(0)
        try:
            from PIL import Image

            with Image.open(image.stream) as uploaded_image:
                uploaded_image.verify()
                detected_format = uploaded_image.format
        except (ImportError, OSError, ValueError):
            flash("That file is not a readable image.", "error")
            return redirect(url_for("admin_projects"))
        if detected_format not in {"JPEG", "PNG", "WEBP"}:
            flash("Use a JPG, PNG, or WebP image.", "error")
            return redirect(url_for("admin_projects"))

        upload_folder = Path(app.config["UPLOAD_FOLDER"])
        upload_folder.mkdir(parents=True, exist_ok=True)
        filename = secure_filename(f"profile-{secrets.token_hex(12)}{extension}")
        image.stream.seek(0)
        image.save(upload_folder / filename)
        image_url = f"/static/uploads/{filename}"
        with get_db() as connection:
            previous = connection.execute(
                "SELECT value FROM site_settings WHERE key = 'profile_image'"
            ).fetchone()
            connection.execute(
                "INSERT INTO site_settings (key, value) VALUES ('profile_image', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (image_url,),
            )
        if previous:
            old_image = BASE_DIR / previous["value"].lstrip("/")
            if old_image.parent == upload_folder and old_image.is_file():
                old_image.unlink()
        flash("Profile picture updated.", "success")
        return redirect(url_for("admin_projects"))

    @app.post("/admin/logout")
    @admin_required
    def admin_logout():
        validate_csrf()
        session.clear()
        return redirect(url_for("admin_login"))

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
