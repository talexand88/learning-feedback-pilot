"""Local preview only. No secrets or passwords are written to disk."""
import getpass
import logging
import secrets
from werkzeug.security import generate_password_hash
from app import create_app

if __name__ == '__main__':
    password = getpass.getpass('Choose a local admin password (at least 12 characters): ')
    if len(password) < 12:
        raise SystemExit('Use at least 12 characters.')
    app = create_app({
        'SECRET_KEY': secrets.token_urlsafe(48),
        'ADMIN_PASSWORD_HASH': generate_password_hash(password, method='pbkdf2:sha256:1000000'),
        'PRODUCTION': False,
    })
    # Suppress development access logs containing IP addresses and URLs.
    logging.getLogger('werkzeug').disabled = True
    print('Forms: http://127.0.0.1:5000/  |  Admin: http://127.0.0.1:5000/admin')
    app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)
