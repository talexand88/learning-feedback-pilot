PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS responses (
 response_id TEXT PRIMARY KEY,
 submitted_at TEXT NOT NULL,
 course_id TEXT NOT NULL,
 course_title TEXT NOT NULL,
 feedback_type TEXT NOT NULL CHECK(feedback_type IN ('video','section')),
 item_id TEXT NOT NULL,
 item_title TEXT NOT NULL,
 q1 TEXT NOT NULL,
 q2 TEXT NOT NULL,
 questionnaire_version TEXT NOT NULL,
 answers_json TEXT NOT NULL,
 questionnaire_json TEXT NOT NULL,
 submission_nonce TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS response_context ON responses(course_id,feedback_type,item_id,submitted_at);
CREATE INDEX IF NOT EXISTS response_date ON responses(submitted_at);
-- Aggregate page requests only. No visitor ID, cookie, IP, or submission linkage.
CREATE TABLE IF NOT EXISTS openings (
 day TEXT NOT NULL, course_id TEXT NOT NULL, feedback_type TEXT NOT NULL,
 item_id TEXT NOT NULL, questionnaire_version TEXT NOT NULL, count INTEGER NOT NULL,
 PRIMARY KEY(day,course_id,feedback_type,item_id,questionnaire_version)
);
