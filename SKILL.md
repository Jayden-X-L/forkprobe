---
name: forkprobe
description: Recommend a small set of candidate skills or artifact-generation pipelines for an open-ended task, then compare their outputs so the user can decide what actually helps. Use when the user is unsure if a particular skill would improve their output, when comparing 2+ skills for the same task, when they naturally ask to compare skills without saying forkprobe, or when explicitly invoked with /forkprobe. Chinese examples include "我想比较几个科研写作 skill", "帮我看看哪个 skill 更适合这段", "先别直接改，并排试几个 skill", "哪个 skill 改出来更自然", and "基于文档做一个 PPT，想比较几个 skill 效果". Especially valuable for academic paragraph polishing, anti-AI text rewriting, scientific writing, reviewer response, Nature-style polishing, PPT planning, and PPTX artifact comparison. Do NOT use for simple deterministic tasks where skill choice is obvious or for casual conversation.
---

# forkprobe

> Stop guessing which AI skill works. See it side by side.

## What this skill does

Recommends a small candidate set for the user's task, then compares completing that task **with** each candidate skill or pipeline versus **without** a skill/pipeline baseline. Candidate recommendation combines local curated candidates with GitHub/network skill discovery by default, then dedupes and scores before asking the user to confirm. For text tasks, it spawns parallel subagents in the current platform (Claude Code or Codex), collects outputs, generates a local HTML report, and lets the user pick the winner. For file-producing tasks such as PPTX, it should compare artifact-generation pipelines and render a report with file links/previews.

**v0.1 scope:** Text-first academic workflows plus first-pass artifact routing for PPTX. Text flows cover paragraph polishing, anti-AI text rewriting, SCI/Nature-style writing, translation/polishing, reviewer-response drafting, and PPT outline comparison. Artifact flows cover PPTX pipeline recommendation and artifact report rendering after candidate files are generated. Candidate discovery merges local curated candidates with sanitized GitHub/network discovery unless the user explicitly asks for local-only/offline mode.

## When to invoke

- User says: "should I use [skill]" / "is [skill] worth it" / "compare with and without skill"
- User asks: "which skill is best for X" (we don't pick — we show)
- User naturally asks to compare skills, even if they do not say "forkprobe"
- User explicitly types `/forkprobe`
- First time encountering a domain where multiple candidate skills exist
- User says they already picked a forkprobe winner and wants to continue, e.g. "我选好了", "已经选好 skill 了", "继续吧", or "用我刚选的继续"

Chinese trigger examples:
- "我想比较几个科研写作 skill"
- "帮我看看哪个 skill 更适合这段"
- "先别直接改，并排试几个 skill"
- "用几个不同 skill 跑一下看看差别"
- "哪个 skill 改出来更自然"
- "帮我评估一下这些 skill 哪个更好"
- "先跑 baseline 和几个写作 skill 对比一下"
- "基于一个文档，我想做一个 PPT，但是想多对比几个 skill 的效果"
- "比较几个 PPT skill，看哪个做出来的 PPT 更好"

## When NOT to invoke

- Simple deterministic tasks where skill choice is obvious
- Conversational / exploratory requests (no comparable artifact)
- User has already picked a skill and just wants to use it

## How to invoke

### Step 1: Understand the task and deliverable type

If the user has not provided enough detail, ask for the task goal and the content to process:
> "你想完成什么任务？请贴上原文或描述目标，我会先推荐一组可对比的 skill。"

Do not require the user to know skill names. Natural task descriptions are enough.

First classify the deliverable:

| User intent | Deliverable type | Compare mode |
|---|---|---|
| polish/rewrite/summarize/rebuttal/PPT outline | `text` or `ppt_outline` | `text` |
| "做一个 PPT", "生成 PPT", "PPTX", "比较 PPT skill 效果" | `pptx` | `artifact` |
| "画图", "生成示意图", "生成图片" | `visual_artifact` | `artifact` |

Important PPT rule:
- If the user says they want to "做一个 PPT" or compare PPT skills, assume they want a **PPTX artifact**.
- Do **not** rewrite the task as "不要生成 PPTX" or "只比较 PPT 方案" unless the user explicitly asks for outline-only output.
- If ambiguous, ask one short clarification: "你要比较最终 PPTX 成品，还是先只比较 PPT 方案/大纲?"

### Step 2: Discover and recommend candidate skills or pipelines

Before running the comparison, recommend 3-5 candidates and wait for user confirmation. Always include `baseline`.

Default discovery flow:
1. Start with local curated candidates from forkprobe's catalog.
2. In parallel, run GitHub/network skill discovery using sanitized task signals such as `academic writing`, `anti-AI writing`, `PPTX artifact`, or `scientific figure`. Do not search with the user's raw document text.
3. Verify discovered GitHub candidates have a `SKILL.md` when possible.
4. Dedupe local/BYO/GitHub candidates by source repo or command arg.
5. Score by task fit, `SKILL.md` availability, popularity, and current environment fit.
6. Present the merged shortlist and ask the user to confirm, remove, or add candidates.

Only skip GitHub/network discovery when the user explicitly asks for local-only/offline candidates, e.g. "只要本地候选", "不要联网", "local only", or "offline".

Use the local recommendation helper when task text is available:

```bash
python scripts/recommend.py --input <path_to_user_input> --domain academic-writing
```

If the user only gave a short task description, use:

```bash
python scripts/recommend.py --text "<task description>" --domain academic-writing
```

If the user explicitly asks for local-only candidates:

```bash
python scripts/recommend.py --text "<task description>" --domain academic-writing --local-only
```

Then present the recommendation in plain language:

```text
我可以并排比较。我会先合并本地 curated 候选和 GitHub/网络发现候选，再让你确认。

根据你的任务，我建议先跑这组：

1. baseline：原始模型输出，作为参照
2. writing-anti-ai：适合降低机器感、让表达更自然
3. research-paper-writing-skills：适合中文科研表达优化
4. paper-writer-skill：适合正式论文语气、IMRAD 结构或审稿回复
5. [GitHub discovered] xxx-writing-skill：社区候选，已发现 SKILL.md，执行前需要确认 license/依赖

确认按这组跑吗？你也可以删掉或加入别的 skill。
```

Recommendation rules:
- If the user already named exact skills, respect that list and only add `baseline` unless they ask for suggestions.
- If the user asks generally to compare skills, recommend first and do not start the run until they confirm.
- If the user does not say local-only/offline, include GitHub/network discovery alongside local candidates.
- For Chinese SCI writing, default toward `baseline`, `writing-anti-ai`, `research-paper-writing-skills`, and `paper-writer-skill`.
- For English/Nature-style polishing or translation, also consider BYO `https://github.com/Yuan1z0825/nature-skills#skills/nature-polishing`.
- For reviewer response/rebuttal tasks, consider `paper-writer-skill` and BYO `https://github.com/Yuan1z0825/nature-skills#skills/nature-response`.
- For PPT outline tasks, compare text plans with `nature-paper2ppt`, `paper-writer-skill`, and relevant writing skills.
- For PPTX artifact tasks, run discovery first, then compare PPT generation pipelines, not writing-only skills.

PPTX discovery:

```bash
python scripts/discover_skills.py \
  --deliverable pptx \
  --query "<task/domain, e.g. academic PPT from document>"
```

The discovery report must classify candidates as:
- `strategy`: improves academic structure/style but needs a generator, e.g. `academic-pptx-skill`, `nature-paper2ppt`
- `generator`: creates/edits PPTX, e.g. `Presentations`, `pptx`
- `full_pipeline`: claims to produce PPTX directly, e.g. `ppt-master`, `md-slides`

Only complete pipelines should enter artifact comparison. Typical scientific PPTX shortlist:
- `baseline + presentations`
- `academic-pptx-skill + presentations`
- `nature-paper2ppt + presentations`
- `ppt-master`
- `md-slides`

Before execution, mark GitHub/external candidates as `needs_verification` until clone/dependency/license/output-path checks pass.

Artifact mode execution:
1. Ask the user to confirm the PPT pipelines.
2. Generate one separate PPTX per pipeline in a clearly named output folder.
3. Render or capture representative previews when possible.
4. Create an artifact manifest JSON and render the artifact report:

```bash
python scripts/render_artifact_report.py \
  --manifest <artifact_manifest.json> \
  --output ./artifact-report.html
```

The artifact report should show file links/previews, candidate summaries, AI judge notes when available, and winner selection.

### Step 3: Confirm skills to compare

Wait for the user to confirm, remove, or add candidates. Also support BYO: user provides a GitHub URL, local path, or `repo#subdir` reference such as:

```text
https://github.com/Yuan1z0825/nature-skills#skills/nature-polishing
```

### Step 4: Run text comparison

For `text` and `ppt_outline` mode, invoke:

```bash
python scripts/compare.py \
  --input <path_to_user_input> \
  --skill <skill_id_1> --skill <skill_id_2> ... \
  --judge \
  --output ./report.html
```

The script:
1. Detects platform (Claude Code vs Codex) via `platform_adapter.py`
2. Spawns N+1 parallel subagents (one per selected skill + baseline)
   - Claude Code: prefers `claude-agent-sdk`, then Anthropic API fallback
   - Codex: prefers native `codex exec` so it inherits Codex Desktop auth/model config, then OpenAI API fallback
3. Each subagent runs the same task input through its respective system prompt
4. Collects outputs, tokens, latency
5. Optionally runs a judge subagent when `--judge` is present
6. Renders HTML via `render_report.py` + `templates/report.html.j2`

For `artifact` mode, do not use `compare.py` directly unless the artifact has first been converted into comparable text summaries. Generate artifacts per pipeline, then use `render_artifact_report.py`.

### Step 5: Show report

Tell the user:
> "Comparison ready. Opening ./report.html — pick the output you prefer."

Auto-open the report (or instruct user how to open it).

### Step 6: Capture verdict

After the user clicks "Pick" in the HTML UI, the verdict is written to:

```
./forkprobe-logs/<timestamp>-<uuid>.json
```

The report also generates a continuation handoff. If the local verdict server is connected, the handoff is written beside the log:

```
./forkprobe-logs/<timestamp>-<uuid>.handoff.md
```

The verdict server also writes stable latest pointers:

```
./forkprobe-logs/latest.json
./forkprobe-logs/latest.handoff.md
```

When the user says they have already picked a winner, do **not** ask them to repeat the skill name first. Run:

```bash
python scripts/resume_verdict.py --latest
```

If a verdict exists, continue using the reported winner and handoff. If no verdict is found, tell the user the page may have been in demo mode, they may have clicked "Pick" without "Submit", or the verdict server may have timed out.

Schema:
```json
{
  "timestamp": "2026-05-28T12:34:56Z",
  "task_type": "academic-polish",
  "platform": "claude_code",
  "task_input_hash": "sha256:...",
  "candidates": [
    {"id": "baseline", "tokens": 480, "latency_s": 3.2},
    {"id": "humanizer", "tokens": 620, "latency_s": 4.1}
  ],
  "judge": {"winner_skill_id": "humanizer", "summary": "..."},
  "verdict": {
    "winner": "humanizer",
    "reason": "...",
    "handoff_text": "Please continue this task using humanizer (humanizer) for the rest of this task..."
  },
  "handoff_path": "./forkprobe-logs/<timestamp>-<uuid>.handoff.md"
}
```

**Note:** `task_input_hash` is the SHA-256 of input, NOT the input itself. **The actual content of user task/output is NEVER stored beyond the local session.**

## Privacy & Safety

- User task content stays local. GitHub/network discovery uses sanitized task signals only, never raw document text.
- If the user asks for local-only/offline mode, skip GitHub/network discovery.
- Verdict logs contain hashes and metadata only — never user task content.
- Handoff files contain the selected winner and user-provided reason, never the original task or candidate outputs.
- For academic users: this is a comparison tool, not a writing assistant. Users are responsible for confirming AI use is permitted by their target journal.

## Architecture

```
SKILL.md (this file)
  └─> scripts/compare.py
        ├─> scripts/platform_adapter.py (Claude Code vs Codex)
        ├─> scripts/recommend.py (local candidate recommendation)
        ├─> scripts/discover_skills.py (PPTX skill/pipeline discovery)
        ├─> scripts/render_artifact_report.py (PPTX/file artifact report rendering)
        ├─> catalog/academic-writing.json (skill metadata)
        └─> scripts/render_report.py
              └─> templates/report.html.j2
```

## See also

- `README.md` — installation and usage from end-user perspective
- `catalog/academic-writing.json` — full curation criteria + selected skills
- `../DESIGN.zh.md` — full project design doc
