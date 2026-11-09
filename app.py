import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timezone, timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, abort, g, redirect, render_template, request, session, url_for, Response
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import check_password_hash

ROOT = Path(__file__).resolve().parent


def validate_config(config):
    from urllib.parse import urlsplit, parse_qs
    for level, questionnaire in config['questionnaires'].items():
        if not (level in ('video', 'section')):
            raise ValueError("Invalid course or questionnaire configuration")
        questions = questionnaire['questions']
        ids = [q['id'] for q in questions]
        if not (len(set(ids)) == len(ids) and 'q1' in ids and 'q2' in ids):
            raise ValueError("Invalid course or questionnaire configuration")
        if not (all(qid.startswith('q') and qid[1:].isdigit() for qid in ids)):
            raise ValueError("Invalid course or questionnaire configuration")
        if not (questions[0]['id'] == 'q1' and questions[0]['type'] == 'choice' and questions[0]['required']):
            raise ValueError("Invalid course or questionnaire configuration")
        if not (questionnaire['version']):
            raise ValueError("Invalid course or questionnaire configuration")
        for question in questions:
            if not (question['type'] in ('choice', 'text')):
                raise ValueError("Invalid course or questionnaire configuration")
            if question['type'] == 'choice':
                if not (question['options'] and len(set(question['options'])) == len(question['options'])):
                    raise ValueError("Invalid course or questionnaire configuration")
            else:
                if not (1 <= question.get('max_length', 2000) <= 2000):
                    raise ValueError("Invalid course or questionnaire configuration")
    def validate_return_url(value):
        if value is None:
            return
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or any(ord(c) <= 32 or ord(c) == 127 for c in value) or "\\" in value):
            raise ValueError('Return destinations must be explicit HTTPS URLs without credentials or whitespace')
        try:
            parsed.port
        except ValueError:
            raise ValueError('Invalid return destination port')

    for cid, course in config['courses'].items():
        validate_return_url(course.get('course_url'))
        for origin in course.get('embed_origins', []):
            validate_return_url(origin)
            parsed = urlsplit(origin)
            if origin != f'https://{parsed.netloc}' or '*' in origin:
                raise ValueError('Embed origins must be exact HTTPS origins without paths or wildcards')
        seen = set()
        for item in course['items']:
            validate_return_url(item.get('video_url'))
            for source, destination in item.get('return_sources', {}).items():
                if not source or len(source) > 80:
                    raise ValueError('Invalid return source identifier')
                validate_return_url(destination.get('video_url'))
                validate_return_url(destination.get('course_url'))
            key = (item['level'], item['id'])
            if not (key not in seen and item['level'] in config['questionnaires']):
                raise ValueError("Invalid course or questionnaire configuration")
            seen.add(key)
            link = urlsplit(item['form_path'])
            if not (not link.scheme and not link.netloc and not link.fragment and link.path == '/feedback'):
                raise ValueError("Invalid course or questionnaire configuration")
            if not (parse_qs(link.query) == dict(course=[cid], level=[item['level']], item=[item['id']])):
                raise ValueError("Invalid course or questionnaire configuration")


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get('SECRET_KEY'),
        ADMIN_PASSWORD_HASH=os.environ.get('ADMIN_PASSWORD_HASH'),
        DATABASE=os.environ.get('DATABASE_PATH', str(ROOT / 'instance' / 'feedback.sqlite3')),
        CONTENT_CONFIG=str(ROOT / 'config.json'),
        PRODUCTION=os.environ.get('APP_ENV') == 'production',
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),
        SESSION_REFRESH_EACH_REQUEST=False, MAX_CONTENT_LENGTH=16384,
    )
    if test_config:
        app.config.update(test_config)
    if not app.config['SECRET_KEY'] or len(app.config['SECRET_KEY']) < 32:
        raise RuntimeError('Set SECRET_KEY to a random secret of at least 32 characters.')
    if not app.config['ADMIN_PASSWORD_HASH']:
        raise RuntimeError('Set ADMIN_PASSWORD_HASH; see README.')
    if app.config['PRODUCTION'] and os.environ.get('SECURITY_APPROVED') != 'yes':
        raise RuntimeError('Production requires approved hosting, privacy, and access controls. See README.')
    app.config['SESSION_COOKIE_SECURE'] = app.config['PRODUCTION']
    config = json.loads(Path(app.config['CONTENT_CONFIG']).read_text())
    validate_config(config)
    signer = URLSafeTimedSerializer(app.config['SECRET_KEY'], salt='feedback-form-v1')
    dbpath = Path(app.config['DATABASE'])
    dbpath.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with sqlite3.connect(dbpath) as conn:
        conn.executescript((ROOT / 'schema.sql').read_text())
    dbpath.chmod(0o600)
    buckets, lock = {}, threading.Lock()

    def db():
        if 'db' not in g:
            g.db = sqlite3.connect(dbpath, timeout=10)
            g.db.row_factory = sqlite3.Row
        return g.db

    @app.teardown_appcontext
    def close_db(error):
        conn = g.pop('db', None)
        if conn:
            conn.close()

    def limited(kind, limit):
        # One-minute, ephemeral HMAC buckets. Never persist IPs or log these keys.
        window = int(time.time() // 60)
        address = request.remote_addr or 'unknown'
        key = hmac.new(app.secret_key.encode(), f'{window}:{kind}:{address}'.encode(), hashlib.sha256).digest()
        with lock:
            for old in list(buckets):
                if buckets[old][0] != window:
                    del buckets[old]
            count = buckets.get(key, (window, 0))[1] + 1
            buckets[key] = (window, count)
        if count > limit:
            abort(429, description='Too many requests. Please try again in a minute.')

    @app.after_request
    def headers(response):
        response.headers.update({
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
            'Content-Security-Policy': "default-src 'none'; style-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
            'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
        })
        if getattr(g, 'section_embed_origins', None):
            ancestors = "'self' " + ' '.join(g.section_embed_origins)
            response.headers['Content-Security-Policy'] = response.headers['Content-Security-Policy'].replace(
                "frame-ancestors 'none'", 'frame-ancestors ' + ancestors)
            response.headers.pop('X-Frame-Options', None)
        if app.config['PRODUCTION']:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    def context():
        for name in ('course', 'level', 'item'):
            if len(request.args.getlist(name)) != 1:
                abort(404)
        cid, level, iid = (request.args.get(k) for k in ('course', 'level', 'item'))
        course = config['courses'].get(cid)
        item = next((i for i in course['items'] if i['id'] == iid and i['level'] == level), None) if course else None
        if not item or level not in config['questionnaires']:
            abort(404, description='This feedback link is unavailable. You can continue your course.')
        if level == 'section' and request.endpoint in ('feedback', 'thanks'):
            g.section_embed_origins = course.get('embed_origins', [])
        return cid, course, item, config['questionnaires'][level]

    def csrf():
        session.setdefault('csrf', secrets.token_urlsafe(32))
        return session['csrf']

    def check_csrf():
        if not session.get('csrf') or not hmac.compare_digest(session['csrf'], request.form.get('csrf', '')):
            abort(400, description='Your session expired. Reload the page and try again.')

    app.jinja_env.globals['csrf'] = csrf

    def protected(fn):
        @wraps(fn)
        def inner(*args, **kwargs):
            if not session.get('admin'):
                return redirect(url_for('login'))
            return fn(*args, **kwargs)
        return inner

    @app.route('/')
    def index():
        return render_template('index.html', config=config)

    def navigation(course, item):
        if len(request.args.getlist('source')) > 1:
            abort(400, description='Use a single return source.')
        source = request.args.get('source', '')
        if source:
            destination = item.get('return_sources', {}).get(source)
            if destination is None:
                abort(400, description='This return source is not configured.')
        else:
            destination = {'video_url': item.get('video_url'), 'course_url': course.get('course_url')}
        links = []
        if item['level'] == 'video' and destination.get('video_url'):
            links.append({'label': 'Back to video', 'url': destination['video_url']})
        if destination.get('course_url'):
            links.append({'label': 'Back to course', 'url': destination['course_url']})
        return source, links

    @app.route('/feedback', methods=['GET', 'POST'])
    def feedback():
        cid, course, item, questionnaire = context()
        limited('feedback', 30)
        source, return_links = navigation(course, item)
        identity = [cid, item['level'], item['id'], questionnaire['version'], source]
        error, values = None, {}
        if request.method == 'POST':
            try:
                token = signer.loads(request.form.get('token', ''), max_age=7200)
                if token['context'] != identity:
                    abort(400)
            except (BadSignature, SignatureExpired, KeyError):
                abort(400, description='This form expired. Reload the link and try again.')
            for q in questionnaire['questions']:
                answer = request.form.get(q['id'], '')
                values[q['id']] = answer
                if len(request.form.getlist(q['id'])) > 1 or (q['required'] and not answer.strip()):
                    error = 'Please answer the required question.'
                if q['type'] == 'choice' and answer and answer not in q['options']:
                    error = 'Choose one of the listed answers.'
                if q['type'] == 'text' and len(answer) > q.get('max_length', 2000):
                    error = 'Your comment is too long. Please use at most 2,000 characters.'
            if not error:
                try:
                    with db() as conn:
                        conn.execute('INSERT INTO responses VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                            str(uuid.uuid4()), datetime.now(timezone.utc).isoformat(timespec='seconds'),
                            cid, course['title'], item['level'], item['id'], item['title'],
                            values['q1'], values.get('q2', ''), questionnaire['version'],
                            json.dumps(values, ensure_ascii=False), json.dumps(questionnaire, ensure_ascii=False), token['nonce']))
                except sqlite3.IntegrityError:
                    # Acknowledge only a nonce already committed successfully.
                    if not db().execute('SELECT 1 FROM responses WHERE submission_nonce=?', (token['nonce'],)).fetchone():
                        raise
                return redirect(url_for('thanks', course=cid, level=item['level'], item=item['id'],
                                        **({'source': source} if source else {})), code=303)
            form_token = request.form.get('token', '')
        else:
            with db() as conn:
                conn.execute('INSERT INTO openings VALUES (?,?,?,?,?,1) ON CONFLICT DO UPDATE SET count=count+1',
                             (datetime.now(timezone.utc).date().isoformat(), cid, item['level'], item['id'], questionnaire['version']))
            form_token = signer.dumps({'context': identity, 'nonce': secrets.token_urlsafe(24)})
        return render_template('feedback.html', course=course, item=item, questionnaire=questionnaire,
                               token=form_token, error=error, values=values, return_links=return_links), (422 if error else 200)

    @app.route('/thanks')
    def thanks():
        q = config['questionnaires'].get(request.args.get('level'))
        if not q:
            abort(404)
        return_links = []
        if 'course' in request.args or 'item' in request.args:
            cid, course, item, q = context()
            _, return_links = navigation(course, item)
        return render_template('thanks.html', message=q['thanks'], return_links=return_links)

    @app.route('/admin/login', methods=['GET', 'POST'])
    def login():
        error = None
        if request.method == 'POST':
            limited('login', 5)
            check_csrf()
            if check_password_hash(app.config['ADMIN_PASSWORD_HASH'], request.form.get('password', '')):
                session.clear()
                session.update(admin=True, csrf=secrets.token_urlsafe(32))
                session.permanent = True
                return redirect(url_for('admin'), code=303)
            error = 'Sign-in failed.'
        return render_template('login.html', error=error), (401 if error else 200)

    @app.post('/admin/logout')
    @protected
    def logout():
        check_csrf()
        session.clear()
        return redirect(url_for('login'), code=303)

    def filters():
        clauses, args = [], []
        for key, column in [('course', 'course_id'), ('level', 'feedback_type'), ('item', 'item_id')]:
            value = request.args.get(key, '')
            if len(value) > 200:
                abort(400)
            if value:
                clauses.append(column + '=?')
                args.append(value)
        dates = {}
        for key in ('start', 'end'):
            value = request.args.get(key, '')
            if value:
                try:
                    dates[key] = datetime.strptime(value, '%Y-%m-%d').date()
                except ValueError:
                    abort(400, description='Use valid dates in YYYY-MM-DD format.')
        if dates.get('start') and dates.get('end') and dates['start'] > dates['end']:
            abort(400, description='Start date must be before end date.')
        if 'start' in dates:
            clauses.append('submitted_at>=?')
            args.append(dates['start'].isoformat())
        if 'end' in dates:
            clauses.append('submitted_at<?')
            args.append((dates['end'] + timedelta(days=1)).isoformat())
        return (' WHERE ' + ' AND '.join(clauses) if clauses else ''), args

    @app.get('/admin')
    @protected
    def admin():
        where, args = filters()
        rows = db().execute('SELECT * FROM responses' + where + ' ORDER BY submitted_at DESC, response_id', args).fetchall()
        ratings = {level: Counter(r['q1'] for r in rows if r['feedback_type'] == level) for level in ('video', 'section')}
        courses = Counter(r['course_title'] for r in rows)
        items = Counter((r['course_title'], r['feedback_type'], r['item_title']) for r in rows)
        trends = sorted(Counter((r['submitted_at'][:10], r['feedback_type']) for r in rows).items())
        # Opening dates are day aggregates, so apply the same inclusive UTC dates.
        opening_where = where.replace('submitted_at', 'day')
        openings = db().execute('SELECT COALESCE(SUM(count),0) FROM openings' + opening_where, args).fetchone()[0]
        choices = db().execute('SELECT DISTINCT course_id,course_title,feedback_type,item_id,item_title FROM responses').fetchall()
        return render_template('admin.html', rows=rows[:250], total=len(rows), ratings=ratings,
                               courses=courses, items=items, trends=trends, openings=openings,
                               config=config, choices=choices)

    @app.get('/admin/export.csv')
    @protected
    def export():
        where, args = filters()
        rows = db().execute('SELECT * FROM responses' + where + ' ORDER BY submitted_at,response_id', args)
        columns = ['response_id','submitted_at','course_id','course_title','feedback_type','item_id',
                   'item_title','q1','q2','questionnaire_version','answers_json','questionnaire_json']
        output = io.StringIO(newline='')
        writer = csv.writer(output, quoting=csv.QUOTE_ALL)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row[c] for c in columns])
        return Response(output.getvalue(), content_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': 'attachment; filename="feedback.csv"'})

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(413)
    @app.errorhandler(429)
    def error_page(error):
        return render_template('error.html', error=error), error.code

    return app
