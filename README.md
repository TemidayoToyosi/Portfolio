# Portfolio

## Run locally

Install the dependencies with `pip install -r requirements.txt`, copy `.env.example` to `.env`, replace its placeholder values with your own settings, then run `python app.py`. The app loads `.env` automatically; values already set in the process environment take precedence. The site is served at `http://127.0.0.1:5000` and the private inbox is at `/admin`.

For Gmail SMTP, use a Google App Password for `SMTP_PASSWORD`; a normal Google account password will not work. Never commit real SMTP credentials, the admin password, or the session secret.

Messages are saved to `instance/portfolio.sqlite3` before the server attempts email delivery. A delivery error is reported to the sender while the message remains available in the admin inbox. Configure `SESSION_COOKIE_SECURE=true` when serving the site over HTTPS. The built-in Flask server is for development; production deployment should use a production WSGI server and HTTPS.