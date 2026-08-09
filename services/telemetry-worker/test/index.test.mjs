import assert from "node:assert/strict";
import test from "node:test";

import { buildPublicStats, handleRequest, validateSelectionEvent } from "../src/index.js";

const VALID_EVENT = {
  schema_version: 1,
  event_id: "3f730b76-f247-4c8c-9c6e-99ef4a1e74bc",
  task_type: "web_landing",
  candidate_skill_names: ["Baseline Web", "Hallmark"],
  final_choice: "Hallmark",
};

class FakeStatement {
  constructor(db, sql) {
    this.db = db;
    this.sql = sql;
    this.args = [];
  }

  bind(...args) {
    this.args = args;
    return this;
  }

  async first() {
    if (this.sql.includes("COUNT(*) AS sample_size")) return { sample_size: this.db.sampleSize };
    return { ok: 1 };
  }

  async all() {
    if (this.sql.includes("GROUP BY c.skill_name")) return { results: this.db.skillRows };
    if (this.sql.includes("GROUP BY a.skill_name")) return { results: this.db.pairRows };
    return { results: [] };
  }
}

class FakeDB {
  constructor() {
    this.sampleSize = 0;
    this.skillRows = [];
    this.pairRows = [];
    this.batches = [];
    this.inserted = true;
  }

  prepare(sql) {
    return new FakeStatement(this, sql);
  }

  async batch(statements) {
    this.batches.push(statements);
    return statements.map((_, index) => ({ meta: { changes: index === 0 && this.inserted ? 1 : 0 } }));
  }
}

test("validates the minimal anonymous selection schema", () => {
  const result = validateSelectionEvent(VALID_EVENT);
  assert.equal(result.ok, true);
  assert.deepEqual(result.event.candidate_skill_names, ["Baseline Web", "Hallmark"]);
});

test("rejects task content, output, reason, paths, and identity fields", () => {
  for (const field of ["task_input", "output", "reason", "local_path", "user_id", "email"]) {
    const result = validateSelectionEvent({ ...VALID_EVENT, [field]: "private" });
    assert.equal(result.ok, false, field);
    assert.match(result.error, /unexpected fields/);
  }
});

test("requires the final choice to be a compared Skill or an alternate verdict", () => {
  assert.equal(validateSelectionEvent({ ...VALID_EVENT, final_choice: "Unknown" }).ok, false);
  assert.equal(validateSelectionEvent({ ...VALID_EVENT, final_choice: "__tie__" }).ok, true);
  assert.equal(validateSelectionEvent({ ...VALID_EVENT, final_choice: "__none__" }).ok, true);
});

test("POST stores an idempotent event and candidate rows", async () => {
  const db = new FakeDB();
  const response = await handleRequest(new Request("https://worker.test/v1/selection-events", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(VALID_EVENT),
  }), { DB: db, MIN_PUBLIC_SAMPLES: "20" });
  assert.equal(response.status, 202);
  assert.deepEqual(await response.json(), { ok: true, accepted: true, duplicate: false });
  assert.equal(db.batches[0].length, 3);

  db.inserted = false;
  const duplicate = await handleRequest(new Request("https://worker.test/v1/selection-events", {
    method: "POST",
    body: JSON.stringify(VALID_EVENT),
  }), { DB: db, MIN_PUBLIC_SAMPLES: "20" });
  assert.equal(duplicate.status, 200);
  assert.deepEqual(await duplicate.json(), { ok: true, accepted: false, duplicate: true });
});

test("stats remain hidden below the public sample threshold", async () => {
  const db = new FakeDB();
  db.sampleSize = 19;
  const response = await handleRequest(
    new Request("https://worker.test/v1/stats?task_type=web_landing"),
    { DB: db, MIN_PUBLIC_SAMPLES: "20" },
  );
  const body = await response.json();
  assert.equal(body.public_eligible, false);
  assert.equal(body.sample_size, 19);
  assert.deepEqual(body.skills, []);
  assert.deepEqual(body.pairwise, []);
});

test("builds Skill and pairwise win rates at the public threshold", () => {
  const stats = buildPublicStats(
    "web_landing",
    20,
    20,
    [{ skill_name: "Hallmark", appearances: 20, wins: 13, ties: 2, rejected_all: 1 }],
    [{ skill_a: "Baseline Web", skill_b: "Hallmark", comparisons: 20, a_wins: 5, b_wins: 13, ties: 1, rejected_all: 1 }],
  );
  assert.equal(stats.public_eligible, true);
  assert.equal(stats.skills[0].win_rate, 0.65);
  assert.equal(stats.pairwise[0].a_win_rate_decisive, 0.2778);
});
