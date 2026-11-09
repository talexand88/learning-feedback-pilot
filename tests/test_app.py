import csv
import io
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from werkzeug.security import generate_password_hash
from app import create_app


class PilotTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = dict(TESTING=True, SECRET_KEY='test-secret-' * 5,
                             ADMIN_PASSWORD_HASH=generate_password_hash('test-password', method='pbkdf2:sha256:1000000'),
                             DATABASE=str(Path(self.temp.name) / 'data.sqlite3'))
        self.app = create_app(self.settings)
        self.client = self.app.test_client()
        self.video = '/feedback?course=aifd&level=video&item=course-overview'
        self.section = '/feedback?course=aifd&level=section&item=networking-for-ai'

    def tearDown(self):
        self.temp.cleanup()

    def token(self, path=None):
        page = self.client.get(path or self.video)
        self.assertEqual(page.status_code, 200)
        return re.search(r'name="token" value="([^"]+)"', page.text)[1]

    def login(self):
        self.client.get('/admin/login')
        with self.client.session_transaction() as sess:
            token = sess['csrf']
        return self.client.post('/admin/login', data={'csrf': token, 'password': 'test-password'})

    def submit(self, path=None, rating='Very clear', comment=''):
        path = path or self.video
        return self.client.post(path, data={'token': self.token(path), 'q1': rating, 'q2': comment})

    def test_both_forms_and_exact_copy(self):
        self.assertIn('Help Us Improve', self.client.get(self.video).text)
        self.assertIn('Your Feedback Matters', self.client.get(self.section).text)
        for path, rating, text in [(self.video, 'Very clear', 'Thank you for your feedback!'),
                                   (self.section, 'Very well', 'Thank you for helping us improve the learning experience!')]:
            result = self.submit(path, rating)
            self.assertEqual(result.status_code, 303)
            self.assertIn(text, self.client.get(result.location).text)

    def test_context_and_validation(self):
        self.assertEqual(self.client.get(self.video + '&course=evil').status_code, 404)
        self.assertEqual(self.client.get('/feedback?course=fake&level=video&item=x').status_code, 404)
        token = self.token()
        for values in ({}, {'q1': 'Excellent'}, {'q1': 'Very clear', 'q2': 'x' * 2001}):
            self.assertEqual(self.client.post(self.video, data=dict(token=token, **values)).status_code, 422)
        self.assertEqual(self.client.post(self.section, data={'token': token, 'q1': 'Very well'}).status_code, 400)
        self.assertEqual(self.client.post(self.video, data={'q1': 'Very clear'}).status_code, 400)
        self.assertEqual(self.client.post(self.video, data={'q2': 'x' * 20000}).status_code, 413)
        with sqlite3.connect(self.settings['DATABASE']) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM responses').fetchone()[0], 0)

    def test_persistence_dedup_and_original_csv(self):
        comment = '  =1+1, "quote"\r\nשיפור 🟢\n  '
        token = self.token()
        for _ in range(2):
            self.assertEqual(self.client.post(self.video, data={'token': token, 'q1': 'Somewhat clear', 'q2': comment}).status_code, 303)
        restarted = create_app(self.settings).test_client()
        self.assertEqual(restarted.get('/admin').status_code, 302)
        self.login()
        raw = self.client.get('/admin/export.csv')
        rows = list(csv.DictReader(io.StringIO(raw.text, newline='')))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['q2'], comment)
        self.assertEqual(rows[0]['course_title'], 'AI Infrastructure and Operations Fundamentals')
        self.assertEqual(json.loads(rows[0]['answers_json'])['q2'], comment)
        self.assertIn('questionnaire_version', rows[0])
        with sqlite3.connect(self.settings['DATABASE']) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM responses').fetchone()[0], 1)

    def test_access_csrf_and_escaping(self):
        for path in ['/admin', '/admin/export.csv']:
            self.assertEqual(self.client.get(path).status_code, 302)
        self.assertEqual(self.client.post('/admin/login', data={'password': 'test-password'}).status_code, 400)
        self.submit(comment='<script>alert(1)</script>')
        self.assertEqual(self.login().status_code, 303)
        page = self.client.get('/admin')
        self.assertIn('&lt;script&gt;', page.text)
        self.assertNotIn('<script>', page.text)
        self.assertIn("default-src 'none'", page.headers['Content-Security-Policy'])
        self.assertEqual(self.client.post('/admin/logout').status_code, 400)
        with self.client.session_transaction() as sess:
            token = sess['csrf']
        self.assertEqual(self.client.post('/admin/logout', data={'csrf': token}).status_code, 303)
        self.assertEqual(self.client.get('/admin/export.csv').status_code, 302)

    def test_filters_and_separate_distributions(self):
        self.submit()
        self.submit(self.section, 'Not at all', 'Add a lab')
        self.login()
        rows = list(csv.DictReader(io.StringIO(self.client.get('/admin/export.csv?level=section').text)))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['feedback_type'], 'section')
        self.assertEqual(len(list(csv.DictReader(io.StringIO(self.client.get('/admin/export.csv?item=none').text)))), 0)
        self.assertEqual(self.client.get('/admin?start=bad').status_code, 400)
        self.assertEqual(self.client.get('/admin?start=2026-12-01&end=2026-01-01').status_code, 400)
        page = self.client.get('/admin').text
        self.assertIn('Video · explanation clarity', page)
        self.assertIn('Section · support for understanding', page)

    def test_openings_are_independent(self):
        self.token()
        self.token()
        self.submit()
        with sqlite3.connect(self.settings['DATABASE']) as db:
            self.assertEqual(db.execute('SELECT SUM(count) FROM openings').fetchone()[0], 3)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM responses').fetchone()[0], 1)

    def test_abuse_limit(self):
        self.client.get('/admin/login')
        with self.client.session_transaction() as sess:
            token = sess['csrf']
        for _ in range(5):
            self.assertEqual(self.client.post('/admin/login', data={'csrf': token, 'password': 'bad'}).status_code, 401)
        self.assertEqual(self.client.post('/admin/login', data={'csrf': token, 'password': 'bad'}).status_code, 429)

    def test_contextual_return_links(self):
        config = json.loads((Path(__file__).resolve().parents[1] / 'config.json').read_text())
        course = config['courses']['aifd']
        course['course_url'] = 'https://academy.example.test/courses/aifd'
        item = course['items'][0]
        item['video_url'] = 'https://video.example.test/overview#t=30s'
        item['return_sources'] = {'alm-section-2': {
            'video_url': 'https://academy.example.test/courses/aifd/section-2/video',
            'course_url': 'https://academy.example.test/courses/aifd/section-2'}}
        path = Path(self.temp.name) / 'returns.json'
        path.write_text(json.dumps(config))
        client = create_app(dict(self.settings, CONTENT_CONFIG=str(path))).test_client()
        page = client.get(self.video)
        self.assertIn('Back to video', page.text)
        self.assertIn(item['video_url'], page.text)
        self.assertIn(course['course_url'], page.text)
        source_path = self.video + '&source=alm-section-2'
        page = client.get(source_path)
        token = re.search(r'name="token" value="([^"]+)"', page.text)[1]
        self.assertIn(item['return_sources']['alm-section-2']['video_url'], page.text)
        self.assertNotIn(item['video_url'], page.text)
        invalid = client.post(self.video, data={'token': token, 'q1': 'Very clear'})
        self.assertEqual(invalid.status_code, 400)
        result = client.post(source_path, data={'token': token, 'q1': 'Very clear'})
        self.assertEqual(result.status_code, 303)
        thanks = client.get(result.location)
        self.assertIn('Back to course', thanks.text)
        self.assertIn(item['return_sources']['alm-section-2']['course_url'], thanks.text)
        self.assertEqual(client.get(self.video + '&source=https://evil.test').status_code, 400)
        self.assertEqual(client.get(source_path + '&source=another').status_code, 400)
        injected = client.get(self.video + '&return_to=https://evil.test', headers={'Referer': 'https://evil.test'})
        self.assertNotIn('https://evil.test', injected.text)

    def test_unsafe_return_configuration_rejected(self):
        config = json.loads((Path(__file__).resolve().parents[1] / 'config.json').read_text())
        path = Path(self.temp.name) / 'unsafe.json'
        for value in ['javascript:alert(1)', '//evil.test/path', 'http://example.test',
                      'https://user:password@example.test', 'https://good.test/\\evil',
                      'https://good.test/\npath']:
            config['courses']['aifd']['course_url'] = value
            path.write_text(json.dumps(config))
            with self.assertRaises(ValueError):
                create_app(dict(self.settings, CONTENT_CONFIG=str(path)))

    def test_section_embedding_is_scoped(self):
        section = self.client.get(self.section)
        self.assertIn('compact-section', section.text)
        self.assertIn('rows="2"', section.text)
        self.assertNotIn('<header>', section.text)
        self.assertNotIn('<footer>', section.text)
        self.assertIn('target="_top"', section.text)
        self.assertNotIn('X-Frame-Options', section.headers)
        self.assertIn("frame-ancestors 'self' https://www.nvidia.com", section.headers['Content-Security-Policy'])
        result = self.submit(self.section, 'Very well')
        thanks = self.client.get(result.location)
        self.assertIn('https://www.nvidia.com', thanks.headers['Content-Security-Policy'])
        for path in [self.video, '/admin/login', '/', '/thanks?level=section']:
            self.assertEqual(self.client.get(path).headers['X-Frame-Options'], 'DENY')

    def test_extra_question(self):
        config = json.loads((Path(__file__).resolve().parents[1] / 'config.json').read_text())
        config['questionnaires']['video']['questions'].append(dict(id='q3', label='Suggestion category', type='choice', required=False, options=['Audio', 'Visual']))
        path = Path(self.temp.name) / 'config.json'
        path.write_text(json.dumps(config))
        app = create_app(dict(self.settings, CONTENT_CONFIG=str(path)))
        client = app.test_client()
        token = re.search(r'name="token" value="([^"]+)"', client.get(self.video).text)[1]
        self.assertEqual(client.post(self.video, data=dict(token=token,q1='Very clear',q3='Audio')).status_code, 303)
        with sqlite3.connect(self.settings['DATABASE']) as db:
            answers = json.loads(db.execute('SELECT answers_json FROM responses').fetchone()[0])
            self.assertEqual(answers['q3'], 'Audio')


if __name__ == '__main__':
    unittest.main()
