# ForkProbe for DeepSeek Harness

`forkprobe-dsh` is ForkProbe's native DeepSeek Harness plugin. It registers two
model-facing tools:

- `forkprobe_compare`: after the user confirms a shortlist, fan out the same
  text task to native DSH subagents, run an optional judge, open the local HTML
  comparison report, and return the user-selected winner.
- `forkprobe_resume`: recover a Report verdict after the first tool's local wait
  window has ended.

The plugin does not start a nested `dsh` process and does not copy DSH
credentials. Python is used only as a deterministic local bridge for existing
ForkProbe Skill loading, Report rendering, Winner handoff, and optional
anonymous telemetry consent.

## Install from GitHub

```bash
dsh plugin --profile web add "github:Jayden-X-L/forkprobe"
```

Install the same package for headless use when needed:

```bash
dsh plugin --profile headless add "github:Jayden-X-L/forkprobe"
```

Restart the selected DSH profile after installation. Requirements: DeepSeek
Harness `0.1.0-rc.6` or newer, Node.js `22.19+`, Python 3, and Jinja2.

The packaged plugin passes the community
[`dsh-plugin-verify`](https://github.com/qing3a/dsh-plugin-verify) checks:
namespace entry shape, Cordis patch placement, the complete 7/7 DSH waterfall,
and `tools/result` completion semantics.

## Minimal prompt

```text
Use ForkProbe to recommend several Chinese writing Skills first. Show me the
shortlist and wait for confirmation. After I confirm, compare them with native
DSH subagents and open the Report so I can choose the winner.
```

The explicit confirmation gate is enforced by `forkprobe_compare`: the tool
rejects calls unless `confirmed=true`.

## Uninstall

```bash
dsh plugin --profile web remove forkprobe-dsh
dsh plugin --profile headless remove forkprobe-dsh
```

## Privacy and permissions

- Candidate tasks execute through DSH's registered native subagent provider.
- Text candidates receive no tools, preventing file mutations and recursive
  ForkProbe calls.
- Report files and result sidecars stay in the active workspace.
- Anonymous Winner sharing remains controlled by the Report consent checkbox;
  only task type, compared Skill names, and the final choice can be uploaded.
- Original task content, files, candidate outputs, and DSH credentials are not
  uploaded by ForkProbe telemetry.
