# forkprobe

> Find the skill that actually helps.

[中文说明](./README.zh-CN.md) | [Launch page](https://jayden-x-l.github.io/forkprobe/)

forkprobe helps you compare multiple AI skills before committing to one. Give an Agent the same task, let forkprobe recommend candidate skills, run them side by side, review an AI judge recommendation, pick the winner, and continue from the selected path.

It is useful when the skill ecosystem is too crowded to guess from descriptions alone: office writing, research polishing, financial analysis, PPT planning, PPTX artifact generation, and other Agent workflows where the right skill matters.

## Why forkprobe

AI skills are multiplying quickly. Many of them sound useful, but the real output can vary by task, language, domain, and model.

forkprobe turns skill choice into a visible workflow:

1. Recommend a small candidate set.
2. Run the same task through baseline and several skills.
3. Generate a local HTML report.
4. Show output quality, latency, token estimates, and an AI judge recommendation.
5. Let the user pick a winner.
6. Generate a continuation handoff so the Agent can keep working from the chosen result.

forkprobe does not write skills for you. It helps you discover, compare, and select skills that already exist.

## Launch Page

The product page lives in `docs/index.html` and is ready for GitHub Pages:

```text
https://jayden-x-l.github.io/forkprobe/
```

## Natural Trigger

You do not have to remember a command. Say something like:

```text
Compare a few skills first and see which one fits the current task better.
```

Or be explicit:

```text
Use forkprobe to recommend candidate skills. After I confirm, run them side by side, generate a report, and let me choose the winner.
```

Chinese trigger:

```text
先帮我比较几个 skill，看看哪个更适合当前任务。
```

Or:

```text
请用 forkprobe 推荐候选，等我确认后再并排执行并生成 report，让我选择 winner。
```

## Supported Agent Workflows

forkprobe currently has implemented execution paths for:

- Claude Code / Claude-style skill sessions
- Codex native execution, with fallback to the OpenAI API

It is also designed to fit natural-language Agent workflows such as OpenClaw, WorkBuddy, OpenCode, and similar platforms through the same candidate recommendation, report, and continuation handoff pattern.

## Installation

Install as a local skill by copying this folder into your Agent skill directory.

Claude Code:

```bash
cp -r forkprobe ~/.claude/skills/
```

Codex / local Agent skill setups:

```bash
cp -r forkprobe ~/.agents/skills/
```

Dependencies:

```bash
pip3 install jinja2 anthropic openai
```

Optional for Claude SDK execution:

```bash
pip3 install claude-agent-sdk
```

## Quick Start

Create an input file:

```bash
echo "Polish this paragraph and keep the meaning unchanged." > /tmp/forkprobe-input.txt
```

Run a local comparison:

```bash
python3 scripts/compare.py \
  --input /tmp/forkprobe-input.txt \
  --skill baseline \
  --skill writing-anti-ai \
  --judge \
  --output /tmp/forkprobe-report.html
```

Open the report:

```bash
open /tmp/forkprobe-report.html
```

## Skill Recommendation

Before running a comparison, forkprobe can recommend candidates:

```bash
python3 scripts/recommend.py --input /tmp/forkprobe-input.txt
```

By default, recommendation combines local curated candidates with GitHub/network discovery using sanitized task signals. It does not send the raw task text as a search query.

For local-only discovery:

```bash
python3 scripts/recommend.py --input /tmp/forkprobe-input.txt --local-only
```

## PPTX Artifact Comparison

For "make a PPT" tasks, forkprobe can route to artifact comparison instead of text-only outline comparison. It can discover strategy skills, generators, and full pipelines, then render a report from generated files:

```bash
python3 scripts/render_artifact_report.py \
  --manifest /tmp/forkprobe-ppt-artifacts.json \
  --output /tmp/forkprobe-ppt-report.html
```

## Privacy

- Task content stays local in the report and local logs.
- GitHub/network discovery uses sanitized task signals, not the raw document.
- Local verdict logs store the selected winner, optional reason, report path, and continuation handoff.
- Use `--local-only` or ask for local-only candidates to skip network discovery.

## Tests

Smoke tests:

```bash
python3 tests/test_smoke.py
```

Integration tests require real model/API access:

```bash
FORKPROBE_RUN_INTEGRATION=1 python3 tests/test_integration.py
```

## Project Structure

```text
docs/       GitHub Pages launch page
scripts/    comparison, recommendation, report, and verdict helpers
templates/  HTML report template
catalog/    curated skill catalogs
tests/      smoke and integration tests
SKILL.md    Agent skill instructions
```

## License

MIT. See [LICENSE](./LICENSE).
