const TASK_TYPE_RE = /^[a-z0-9][a-z0-9_:-]{0,63}$/;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const MAX_BODY_BYTES = 16 * 1024;
const MAX_CANDIDATES = 10;
const MAX_NAME_LENGTH = 128;
const ALLOWED_FIELDS = new Set([
  "schema_version",
  "event_id",
  "task_type",
  "candidate_skill_names",
  "final_choice",
]);

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "no-store",
    },
  });
}

function cleanName(value) {
  return typeof value === "string" ? value.trim().replace(/\s+/g, " ") : "";
}

export function validateSelectionEvent(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return { ok: false, error: "body must be a JSON object" };
  }
  const unexpected = Object.keys(value).filter((key) => !ALLOWED_FIELDS.has(key));
  if (unexpected.length) {
    return { ok: false, error: `unexpected fields: ${unexpected.join(", ")}` };
  }
  if (value.schema_version !== 1) {
    return { ok: false, error: "unsupported schema_version" };
  }
  if (!UUID_RE.test(String(value.event_id || ""))) {
    return { ok: false, error: "event_id must be a UUID" };
  }
  const taskType = String(value.task_type || "").trim().toLowerCase();
  if (!TASK_TYPE_RE.test(taskType)) {
    return { ok: false, error: "invalid task_type" };
  }
  if (!Array.isArray(value.candidate_skill_names) || value.candidate_skill_names.length < 1 || value.candidate_skill_names.length > MAX_CANDIDATES) {
    return { ok: false, error: `candidate_skill_names must contain 1-${MAX_CANDIDATES} names` };
  }
  const candidates = value.candidate_skill_names.map(cleanName);
  if (candidates.some((name) => !name || name.length > MAX_NAME_LENGTH)) {
    return { ok: false, error: `Skill names must contain 1-${MAX_NAME_LENGTH} characters` };
  }
  const deduped = new Set(candidates.map((name) => name.toLocaleLowerCase("en-US")));
  if (deduped.size !== candidates.length) {
    return { ok: false, error: "candidate Skill names must be unique" };
  }
  const finalChoice = cleanName(value.final_choice);
  if (!["__tie__", "__none__"].includes(finalChoice) && !candidates.includes(finalChoice)) {
    return { ok: false, error: "final_choice must be a compared Skill name, __tie__, or __none__" };
  }
  return {
    ok: true,
    event: {
      schema_version: 1,
      event_id: String(value.event_id).toLowerCase(),
      task_type: taskType,
      candidate_skill_names: candidates,
      final_choice: finalChoice,
    },
  };
}

function canonicalEvent(event) {
  return JSON.stringify({
    schema_version: event.schema_version,
    event_id: event.event_id,
    task_type: event.task_type,
    candidate_skill_names: [...event.candidate_skill_names].sort(),
    final_choice: event.final_choice,
  });
}

async function sha256(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function resultRows(result) {
  return Array.isArray(result?.results) ? result.results : [];
}

export function buildPublicStats(taskType, sampleSize, minimum, skillRows, pairRows) {
  const publicEligible = sampleSize >= minimum;
  return {
    task_type: taskType,
    sample_size: sampleSize,
    minimum_public_samples: minimum,
    public_eligible: publicEligible,
    skills: publicEligible ? skillRows.map((row) => ({
      skill_name: row.skill_name,
      appearances: Number(row.appearances || 0),
      wins: Number(row.wins || 0),
      ties: Number(row.ties || 0),
      rejected_all: Number(row.rejected_all || 0),
      win_rate: Number(row.appearances || 0) > 0
        ? Number((Number(row.wins || 0) / Number(row.appearances)).toFixed(4))
        : 0,
    })) : [],
    pairwise: publicEligible ? pairRows.map((row) => {
      const decisive = Number(row.a_wins || 0) + Number(row.b_wins || 0);
      return {
        skill_a: row.skill_a,
        skill_b: row.skill_b,
        comparisons: Number(row.comparisons || 0),
        a_wins: Number(row.a_wins || 0),
        b_wins: Number(row.b_wins || 0),
        ties: Number(row.ties || 0),
        rejected_all: Number(row.rejected_all || 0),
        a_win_rate_decisive: decisive > 0 ? Number((Number(row.a_wins || 0) / decisive).toFixed(4)) : null,
      };
    }) : [],
  };
}

async function postSelectionEvent(request, env) {
  if (env.SELECTION_RATE_LIMITER) {
    const rateLimitKey = request.headers.get("CF-Connecting-IP") || "unknown";
    const { success } = await env.SELECTION_RATE_LIMITER.limit({ key: rateLimitKey });
    if (!success) {
      return jsonResponse({ error: "rate limit exceeded" }, 429);
    }
  }
  const contentLength = Number(request.headers.get("content-length") || 0);
  if (contentLength > MAX_BODY_BYTES) {
    return jsonResponse({ error: "request body too large" }, 413);
  }
  let raw;
  try {
    const text = await request.text();
    if (new TextEncoder().encode(text).byteLength > MAX_BODY_BYTES) {
      return jsonResponse({ error: "request body too large" }, 413);
    }
    raw = JSON.parse(text);
  } catch {
    return jsonResponse({ error: "invalid JSON" }, 400);
  }
  const validated = validateSelectionEvent(raw);
  if (!validated.ok) {
    return jsonResponse({ error: validated.error }, 400);
  }
  const event = validated.event;
  const payloadHash = await sha256(canonicalEvent(event));
  const statements = [
    env.DB.prepare(
      "INSERT OR IGNORE INTO selection_events " +
      "(event_id, schema_version, payload_hash, task_type, final_choice) VALUES (?, ?, ?, ?, ?)"
    ).bind(event.event_id, event.schema_version, payloadHash, event.task_type, event.final_choice),
    ...event.candidate_skill_names.map((skillName) => env.DB.prepare(
      "INSERT OR IGNORE INTO event_candidates (event_id, skill_name) " +
      "SELECT event_id, ? FROM selection_events WHERE event_id = ? AND payload_hash = ?"
    ).bind(skillName, event.event_id, payloadHash)),
  ];
  const results = await env.DB.batch(statements);
  const inserted = Number(results?.[0]?.meta?.changes || 0) === 1;
  return jsonResponse({ ok: true, accepted: inserted, duplicate: !inserted }, inserted ? 202 : 200);
}

async function getStats(url, env) {
  const taskType = String(url.searchParams.get("task_type") || "").trim().toLowerCase();
  if (!TASK_TYPE_RE.test(taskType)) {
    return jsonResponse({ error: "valid task_type is required" }, 400);
  }
  const minimum = Math.max(1, Math.min(1000, Number(env.MIN_PUBLIC_SAMPLES || 20)));
  const sample = await env.DB.prepare(
    "SELECT COUNT(*) AS sample_size FROM selection_events WHERE task_type = ?"
  ).bind(taskType).first();
  const sampleSize = Number(sample?.sample_size || 0);
  if (sampleSize < minimum) {
    return jsonResponse(buildPublicStats(taskType, sampleSize, minimum, [], []));
  }

  const skillResult = await env.DB.prepare(`
    SELECT c.skill_name,
           COUNT(*) AS appearances,
           SUM(CASE WHEN e.final_choice = c.skill_name THEN 1 ELSE 0 END) AS wins,
           SUM(CASE WHEN e.final_choice = '__tie__' THEN 1 ELSE 0 END) AS ties,
           SUM(CASE WHEN e.final_choice = '__none__' THEN 1 ELSE 0 END) AS rejected_all
    FROM event_candidates c
    JOIN selection_events e ON e.event_id = c.event_id
    WHERE e.task_type = ?
    GROUP BY c.skill_name
    ORDER BY wins DESC, appearances DESC, c.skill_name ASC
  `).bind(taskType).all();
  const pairResult = await env.DB.prepare(`
    SELECT a.skill_name AS skill_a,
           b.skill_name AS skill_b,
           COUNT(*) AS comparisons,
           SUM(CASE WHEN e.final_choice = a.skill_name THEN 1 ELSE 0 END) AS a_wins,
           SUM(CASE WHEN e.final_choice = b.skill_name THEN 1 ELSE 0 END) AS b_wins,
           SUM(CASE WHEN e.final_choice = '__tie__' THEN 1 ELSE 0 END) AS ties,
           SUM(CASE WHEN e.final_choice = '__none__' THEN 1 ELSE 0 END) AS rejected_all
    FROM event_candidates a
    JOIN event_candidates b ON a.event_id = b.event_id AND a.skill_name < b.skill_name
    JOIN selection_events e ON e.event_id = a.event_id
    WHERE e.task_type = ?
    GROUP BY a.skill_name, b.skill_name
    ORDER BY comparisons DESC, a.skill_name ASC, b.skill_name ASC
  `).bind(taskType).all();
  return jsonResponse(buildPublicStats(
    taskType,
    sampleSize,
    minimum,
    resultRows(skillResult),
    resultRows(pairResult),
  ));
}

export async function handleRequest(request, env) {
  const url = new URL(request.url);
  if (request.method === "OPTIONS") {
    return new Response(null, {
      status: 204,
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
      },
    });
  }
  if (request.method === "GET" && url.pathname === "/health") {
    await env.DB.prepare("SELECT 1 AS ok").first();
    return jsonResponse({ status: "ready" });
  }
  if (request.method === "POST" && url.pathname === "/v1/selection-events") {
    return postSelectionEvent(request, env);
  }
  if (request.method === "GET" && url.pathname === "/v1/stats") {
    return getStats(url, env);
  }
  return jsonResponse({ error: "not found" }, 404);
}

export default {
  fetch(request, env) {
    return handleRequest(request, env);
  },
};
