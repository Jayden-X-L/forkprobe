PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS selection_events (
  event_id TEXT PRIMARY KEY,
  schema_version INTEGER NOT NULL,
  payload_hash TEXT NOT NULL,
  task_type TEXT NOT NULL,
  final_choice TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS event_candidates (
  event_id TEXT NOT NULL,
  skill_name TEXT NOT NULL,
  PRIMARY KEY (event_id, skill_name),
  FOREIGN KEY (event_id) REFERENCES selection_events(event_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_selection_events_task_type
  ON selection_events(task_type);

CREATE INDEX IF NOT EXISTS idx_event_candidates_skill_name
  ON event_candidates(skill_name);
