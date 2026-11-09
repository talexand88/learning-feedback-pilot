# Learning Experience Feedback Collection System

A local, functional research MVP for NVIDIA Academy. No external deployment has been made and no organizational infrastructure or brand approval is assumed. Plain text naming and green/black styling use no logo, font downloads, or other unapproved brand assets. Learning-item titles below are illustrative; confirm the actual course inventory before launch.

## Architecture and recommended stack

One Python Flask application renders accessible HTML with CSS, validates submissions, and protects the reviewer dashboard. SQLite stores responses on the server's persistent disk. The browser never receives a database credential or administrative records through learner routes. No external scripts, analytics, ALM APIs, Vimeo APIs, or AI services are used.

```text
Vimeo button / ALM link → GET /feedback?course=...&level=...&item=...
                       → validated configuration → optional learner form
                       → signed POST → SQLite response → thank-you page
Authorized reviewer    → admin password + signed session → dashboard / CSV
```

SQLite is a practical candidate for a low-volume, single-instance pilot, subject to approval. It needs local persistent disk, not an ephemeral container filesystem or a shared network drive. If an approved managed database or multiple instances are required, adapt the repository layer to that database before deployment. This MVP intentionally does not provision infrastructure. See [SQLite deployment guidance](https://www.sqlite.org/whentouse.html) and [Flask security guidance](https://flask.palletsprojects.com/en/stable/web-security/).

## Decisions requiring organizational approval before live collection

| Decision | Concrete decision needed |
|---|---|
| Hosting and database | Approved internal host, network exposure, data region, local persistent SQLite versus an approved managed relational database |
| Reviewer identity | Approved SSO/MFA gateway and reviewer group, or explicit approval of the local shared-password mechanism for this pilot; per-reviewer audit is not implemented |
| Storage and backups | Encrypted volume and encrypted backups, service-account permissions, secret manager, restore owner and restore testing |
| Privacy and retention | Authorized reviewers, comment-handling process, retention duration, deletion procedure, privacy copy, export handling |
| Request logging | Proxy, load balancer, host, WAF, and error reporting configuration that avoids learner IPs, cookies, bodies, and unnecessary identifiers |
| Abuse control | Approved gateway controls if stronger prevention than the built-in single-process limits is needed |
| Course and brand | Confirm titles, invitations, intended audience, and brand approval; sample items are not a verified course manifest |

Do not collect live learner data until these are resolved. SQLite is not encrypted by this application. Production startup requires `SECURITY_APPROVED=yes` as an operator acknowledgement, not as evidence that a security review occurred.

## Run locally

Use an organization-supported Python version (recommended Python 3.12+). The tests were also run with the workspace's Python 3.9 runtime.

From this directory:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run_local.py
```

Enter a local admin password at the prompt. Open [local preview](http://127.0.0.1:5000/) and [dashboard](http://127.0.0.1:5000/admin). Preview binds only to localhost. The random session secret and chosen password exist only for that run; restarting invalidates sessions but preserves database records in `instance/feedback.sqlite3`. Choose your password again on restart. Do not use the Flask development server for deployment.

Direct examples:

- `/feedback?course=aifd&level=video&item=course-overview`
- `/feedback?course=aifd&level=section&item=networking-for-ai`

Configure Vimeo/ALM links to open in a new tab where supported so learners retain their course tab. The form never attempts to redirect to an unvalidated return URL. It needs no learner login or learner cookie and works without JavaScript.

## Configuration

`config.json` is the allowlist for courses, items, and questionnaires. The server rejects unknown contexts and duplicated URL context parameters; display titles and stored metadata always come from configuration.

To add a video to `courses.aifd.items`:

```json
{"level":"video","id":"gpu-basics","title":"GPU Basics","form_path":"/feedback?course=aifd&level=video&item=gpu-basics"}
```

To add a section:

```json
{"level":"section","id":"compute-for-ai","title":"Compute for AI","form_path":"/feedback?course=aifd&level=section&item=compute-for-ai"}
```

To add a course, add a new entry under `courses`:

```json
"example-course": {
  "title": "Example Course",
  "items": [
    {"level":"video","id":"welcome","title":"Welcome","form_path":"/feedback?course=example-course&level=video&item=welcome"}
  ]
}
```

`form_path` configures each item's relative form link. Prefix it with the approved application's origin in the Vimeo/ALM authoring tool. Startup checks that every link corresponds to its configured item. Restart the app after editing configuration.

Add questions to `questionnaires.video.questions` or `.section.questions`. Supported types are `choice` with `options` and `text` with `max_length` (maximum 2,000). Each question needs a unique ID such as `q3`, a label, and `required`. Keep `q1` as the required rating and `q2` as the optional comment. Increment the questionnaire version whenever questions, wording, or options change. Old rows keep the full questionnaire snapshot and all answers; new fields appear in the dashboard's “All answers” and CSV JSON fields. Old open forms become invalid after a version change and must be reloaded.

## Schema and research interpretation

`schema.sql` initializes an actual persistent SQLite database and indexes. Each response includes UUID, UTC timestamp, configured course ID/title, level, item ID/title, verbatim q1 and q2, questionnaire version, all answers as JSON, and a questionnaire snapshot. A random per-form nonce enforces one successful submission per rendered form; it is not a learner identifier and is excluded from CSV. A reopened form can be submitted again; unique learners cannot be inferred.

Opening counts are aggregated by UTC day, context, and questionnaire version. No opening-to-submission linkage is stored. Reloads, retries, and bots can increase openings; counts measure requests, not unique people or invitation exposures. Validation failures and resubmitted POSTs do not increment openings.

Dashboard summaries honor course/type/item/inclusive UTC date filters. Rating counts remain separate for videos (clarity) and sections (perceived support). Trends show daily counts by feedback type. There is no combined score, response-rate calculation, sentiment inference, or claim of learning effectiveness. Version changes should be examined separately using the CSV. Historical labels are retained; archived course/item contexts remain selectable from recorded responses. Item IDs are filtered within the selected course/type; choose both to disambiguate repeated IDs.

The detailed table displays the latest 250 matching responses; summaries and CSV cover all matches. This implementation reads matching records in memory for summaries/export, appropriate for a small pilot. Add database-side aggregation and pagination if volume grows substantially.

CSV uses RFC-style quoting and UTF-8, preserving whitespace, line breaks, Unicode, and formula-like text. Import all columns as **text** in spreadsheet software. Quoting alone does not neutralize spreadsheet formulas. No apostrophe or other transformation is added to original research text.

## Security properties and limits

- Parameterized SQL, configured answer allowlists, body and comment limits, escaped HTML, signed context-bound expiring form tokens, and nonce deduplication.
- Admin password is stored only as a hash in server configuration. Sign-in and logout use CSRF tokens. Admin sessions expire after 30 minutes and use HttpOnly/SameSite cookies; production adds Secure cookies and HSTS. Logout clears the browser cookie; individual stolen signed-cookie sessions cannot be centrally revoked. Rotate `SECRET_KEY` to invalidate all sessions.
- Baseline CSP, frame denial, no-referrer policy, and no-store responses. No cross-origin access is enabled.
- No names, emails, learner IDs, raw IPs, analytics identifiers, or user agents are persisted by the app. Free text may still contain personal information volunteered by a learner; use the approved review/deletion process.
- Rate limiting: 30 form GET/POST requests and 5 login attempts per minute per connection IP. Only ephemeral one-minute HMAC keys live in process memory. No limiter keys are written to logs/database. This is basic abuse prevention, not bot protection.
- Run exactly one worker for consistent limiter state. Behind a proxy, the application sees the proxy's address unless explicitly and safely configured otherwise. Shared limits may throttle all learners behind that proxy; have the approved edge apply per-client limits and configure trusted forwarding precisely before live launch. The code intentionally does not trust arbitrary forwarded headers.
- Database file mode is 0600; the default new instance directory is 0700. Use service `umask 077` to cover SQLite WAL/SHM and backup files. Operators must enforce encrypted storage, backups, network isolation, and access review.
- Local runner disables request access logs. Deployment must disable access logs at the WSGI server and upstream layers. Do not enable debug mode or request-body capture in monitoring. Application errors may reach server error logs; apply approved restricted access and retention.

## Deployment procedure after approval

1. Provision an approved host with a supported Python runtime, HTTPS reverse proxy, encrypted persistent **local** data volume and backups. Set the process to run as a dedicated non-root user with `umask 077`; source code should be read-only to that account.
2. Install locked dependencies from the organization's approved package source. Keep runtime/database files outside the static and source directories; expose only Flask routes.
3. Store `SECRET_KEY` (generate with `secrets.token_urlsafe(48)`) and `ADMIN_PASSWORD_HASH` in the approved secret manager. Generate the hash interactively, without putting the password in shell history:

   ```sh
   .venv/bin/python -c 'import getpass; from werkzeug.security import generate_password_hash; print(generate_password_hash(getpass.getpass(), method="pbkdf2:sha256:1000000"))'
   ```

4. Inject environment variables through the host's secret/config mechanism:

   ```text
   APP_ENV=production
   SECRET_KEY=<random persistent secret of at least 32 characters>
   ADMIN_PASSWORD_HASH=<generated password hash>
   DATABASE_PATH=<absolute path on approved persistent volume>/feedback.sqlite3
   SECURITY_APPROVED=yes
   ```

5. Put `/admin` and `/admin/*` behind the approved reviewer access control, including CSV. If integrating SSO, let the gateway enforce authentication and group membership; the application retains its password gate until an approved integration replaces it. Never accept a public user-supplied identity header. Public learner routes remain anonymous.
6. Run behind the HTTPS proxy, on loopback, with one worker and no access log:

   ```sh
   .venv/bin/gunicorn --workers 1 --threads 4 --bind 127.0.0.1:8000 'app:create_app()'
   ```

   Gunicorn access logging is disabled by default; do not add `--access-logfile`. Restrict direct access to this port. Preserve the original Host, reject unexpected hosts at the proxy, enforce HTTPS before forwarding traffic, disable upstream learner request logging, and configure edge rate limiting. Production Secure cookies require HTTPS.

7. Run tests and a controlled end-to-end check with synthetic data through the actual proxy: both Vimeo/ALM links, mobile and keyboard use, all filter combinations, CSV import as text, unauthorized admin requests, restart persistence, backups and restore. Confirm no IP/body/cookie capture anywhere. Check final privacy and brand copy with the designated owners.
8. Verify scheduled encrypted backups using SQLite's online backup API or the approved equivalent (do not copy only the main file while WAL is active). Set an approved retention schedule covering the database, opening aggregates, exports and backups. No retention policy or deletion schedule is assumed or implemented. For approved deletions use a restricted maintenance process keyed by response UUID and include backup expiry; ordinary SQL DELETE does not guarantee physical erasure.

No container image, remote service, admin identity provider, or infrastructure credential is included. Live SSO configuration and infrastructure protection require the approved environment.

## Tests and acceptance

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Eleven automated integration tests cover both form variants, required/optional answers, invalid context and tokens, payload limits, durable database reopening, duplicate handling, admin authentication and CSRF, escaping, filters, opening aggregates, rate limiting, added questions, and verbatim CSV export. Tests use temporary databases and do not create pilot responses. Browser visual inspection, assistive-technology testing, actual SSO/proxy verification, and backup restore verification remain deployment acceptance tasks.

Files: `app.py` server; `templates/` HTML; `static/style.css` responsive styling; `config.json` content; `schema.sql` storage; `tests/test_app.py` integration tests; `run_local.py` local launcher; `requirements.txt` tested dependency versions.

## Return to the originating video and course

The form and thank-you page support “Back to video” and “Back to course” links. Every course and video can have different URLs. Add `course_url` to each course and `video_url` to each video item in `config.json`. Only configured links are rendered; no destinations are inferred from HTTP Referer, browser history, or arbitrary `return_to` parameters. Unconfigured buttons are omitted rather than pointing to invented pages.

For example (illustrative domains only; replace with the actual approved addresses):

```json
"aifd": {
  "title": "AI Infrastructure and Operations Fundamentals",
  "course_url": "https://academy.example.test/courses/aifd",
  "items": [{
    "level": "video", "id": "course-overview", "title": "Course Overview",
    "form_path": "/feedback?course=aifd&level=video&item=course-overview",
    "video_url": "https://academy.example.test/courses/aifd/videos/overview",
    "return_sources": {
      "alm-networking": {
        "video_url": "https://academy.example.test/courses/aifd/networking/overview",
        "course_url": "https://academy.example.test/courses/aifd/networking"
      }
    }
  }]
}
```

The default form link uses the configured course/video destinations. For multiple launch locations for the same item, append `&source=alm-networking`. Each source selects an exact pair of configured URLs, not a user-supplied destination URL. Source-specific pairs do not silently fall back to a different location when a URL is absent. The signed form token includes the source to preserve it through submission; the thank-you page resolves the same context. No source or return URLs are added to response records.

A launch link for each learning item can be authored or generated when publishing the course. Sharing a Vimeo video across courses still requires the launch link to convey the correct course/item/source. A single static Vimeo CTA cannot discover arbitrary embedding ALM pages by itself. If ALM supports dynamic link generation or a course-side wrapper, that component can supply the source identifier; otherwise use separate configured launch links. Reliable automatic capture of an unknown ALM page requires support from that page and is not implemented here.

Navigation returns to the exact configured address, including its query/fragment. Playback position and ALM progress restoration depend on supported deep links, existing authentication, and the player/LMS; this app does not capture playback state. Return URLs must be approved HTTPS destinations without embedded credentials or personal/session identifiers. Startup validates URL structure. Configuration maintainers are responsible for approving destination domains. For many courses, generate this configuration from a course manifest rather than hand-maintaining links.

Ten integration tests now include contextual navigation through submission and the thank-you page, source tampering, untrusted referrers, and unsafe configured URLs. For the pilot, the aifd course return destination is configured as https://www.nvidia.com/en-us/training/academy/course-player/?id=course:15139841. Both feedback types and their thank-you pages show Back to course. Video-specific destinations remain optional and unconfigured.


## Compact embedded section form

Section feedback uses a compact layout automatically, including its thank-you page: no global header/footer, reduced spacing, four rating columns (two under 480px), a two-row resizable comment field, and adjacent submit/return controls. Wording, optional participation, keyboard focus and validation remain available. Very small windows can still scroll; no fixed-height clipping is used. The standalone video form retains its existing layout.

`courses.aifd.embed_origins` permits section form/thank-you embedding from `https://www.nvidia.com` and the app's own origin only. Other pages, including admin and video forms, retain frame denial. Configure each actual ancestor origin if the ALM embed uses an additional host or nested frames; no wildcard domains are allowed. See [CSP frame-ancestors](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/frame-ancestors). A hosted HTTPS form and course-side permission to embed it are still required for live use; localhost is only a preview.

If the course sandboxes its iframe, allow form submissions (`allow-forms`); the return link uses `_top` so it returns the whole window to the course, which requires `allow-top-navigation-by-user-activation` in a sandboxed frame. No automatic top navigation is performed. Browser checks in the actual ALM window remain necessary. Eleven integration tests include section embedding policy isolation.

Local Chrome visual checks: at 640px viewport width, the section form content occupies approximately 408px in height; at 390px, approximately 523px. Both widths have no horizontal overflow. These measurements apply to the current sample title and English text; long titles, validation errors, or added questions can increase height. Actual ALM integration is still to be verified.
