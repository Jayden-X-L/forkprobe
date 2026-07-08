# ForkProbe：AI Skill 选型与试跑工具

<p align="center">
  <strong>别猜哪个 AI Skill 有用，直接并排看结果。</strong>
</p>

<p align="center">
  <a href="https://jayden-x-l.github.io/forkprobe/?lang=zh">发布页</a>
  ·
  <a href="./README.en.md">English README</a>
  ·
  <a href="https://jayden-x-l.github.io/forkprobe/downloads/forkprobe-skill.zip">下载 skill zip</a>
</p>

<p align="center">
  <img alt="MIT License" src="https://img.shields.io/badge/license-MIT-111827">
  <img alt="Version v0.4" src="https://img.shields.io/badge/version-v0.4-2563eb">
  <img alt="Local first reports" src="https://img.shields.io/badge/report-local--first-0f9f8f">
  <img alt="Agent skill selector" src="https://img.shields.io/badge/agent-skill%20selector-2563eb">
</p>

ForkProbe 是一个 AI Skill 选型与试跑工具。它会把同一个任务交给模型本身和多个候选 skill，并排试跑，生成本地 HTML report，让你看到真实输出之后再选择 winner。

**v0.4 新增去 AI 味写作候选池：** 自然化与风格改写会优先比较 `writing-anti-ai`、`humanizer-zh`、`humanizer`、`stop-slop`、`avoid-ai-writing`、`remove-ai-flavor-writing-skill` 等专门的 anti-AI / humanizer skill，并保留 `patina`、`HumanAI` 等多语言扩展候选。v0.3 的市场调研 / 调研报告对比继续支持 report preview、sources.json、evidence table、claim checks、limitations 和 AI 评审建议。

当网络上的 skill 越来越多时，问题不再是“有没有 skill”，而是“当前任务到底该用哪个 skill”。ForkProbe 的目标很直接：先把结果摊开，再让 Agent 沿着你选中的路径继续工作。

## 什么时候该用 ForkProbe

- 你不确定当前任务该用哪个 skill，想先看真实输出再决定。
- 你想比较 baseline 和多个 skill，而不是只相信 skill 的描述。
- 你的交付物是 PPTX、科研 figure package、调研报告 package 这类文件成品，需要看文件、预览和 QA。
- 你想引入 GitHub 或本地自带的 BYO skill，但希望先做一次小规模试跑。
- 不适合简单确定性任务：如果答案或工具路径已经很明确，直接执行会更快。

## 它怎么工作

```mermaid
flowchart LR
  A["你的任务"] --> B["候选 skills / pipelines"]
  B --> C["并行试跑"]
  C --> D["本地 report"]
  D --> E["AI 评审建议"]
  E --> F["你选择 winner"]
  F --> G["Continuation handoff"]
```

ForkProbe 把 skill 选择变成一个可观察的流程：

1. 根据当前任务推荐少量候选 skill 或 artifact pipeline。
2. 用同一份输入跑 baseline 和多个候选。
3. 展示每一路完整输出、耗时、token 估算、文件预览和 AI 评审建议。
4. 由你选择 winner。
5. 生成 continuation handoff，让 Agent 继续执行正式任务。

## 一句话触发

你不需要记命令。直接对 Agent 说：

```text
先帮我比较几个 skill，看看哪个更适合当前任务。
```

或者更明确一点：

```text
请用 forkprobe 推荐候选，等我确认后再并排执行并生成 report，让我选择 winner。
```

英文触发：

```text
Compare a few skills first and see which one fits the current task better.
```

## 能力矩阵与候选推荐

候选推荐严格跟当前 README 能力矩阵对齐。`baseline` 表示不使用额外 skill 的参照组；`+ presentations`、`+ Python/SVG renderer` 表示策略 skill 需要搭配生成器形成完整成品 pipeline。外部 GitHub 候选进入执行前仍建议检查 license、依赖和最终产物路径。

| 场景 | 状态 | Report 里看到什么 | 推荐候选 |
|---|---|---|---|
| 学术润色与 SCI 写作 | 已支持 | 多版本文本、AI 评审、winner 选择 | `baseline`, `research-paper-writing-skills`, `paper-writer-skill`, [`nature-polishing`](https://github.com/Yuan1z0825/nature-skills/tree/main/skills/nature-polishing), `humanizer`, `academic-humanizer` |
| 自然化与风格改写 / 去 AI 味写作 | 已支持 | 不同风格稿件并排比较 | `baseline`, `writing-anti-ai`, [`Humanizer-zh`](https://github.com/op7418/Humanizer-zh), [`humanizer`](https://github.com/blader/humanizer), [`stop-slop`](https://github.com/hardikpandya/stop-slop), [`avoid-ai-writing`](https://github.com/conorbronsdon/avoid-ai-writing), [`remove-ai-flavor-writing-skill`](https://github.com/B1lli/remove-ai-flavor-writing-skill) |
| 审稿回复与投稿材料 | 已支持 | 回复草稿、结构、语气对比 | `baseline`, [`nature-response`](https://github.com/Yuan1z0825/nature-skills/tree/main/skills/nature-response), `paper-writer-skill`, `writing-anti-ai`, `research-paper-writing-skills` |
| PPTX 成品生成 | 已支持 | 可打开的 PPTX、预览图、候选说明 | `baseline + presentations`, [`nature-paper2ppt`](https://github.com/Yuan1z0825/nature-skills/tree/main/skills/nature-paper2ppt) `+ presentations`, [`academic-pptx-skill`](https://github.com/Gabberflast/academic-pptx-skill) `+ presentations`, [`ppt-master`](https://github.com/hugohe3/ppt-master), [`md-slides`](https://github.com/zl190/md-slides) |
| 论文作图 / 科研绘图 | 已支持 | PNG 预览、SVG/PDF/TIFF、代码、caption、QA | `baseline-python-figure`, [`scientific-visualization`](https://github.com/K-Dense-AI/scientific-agent-skills/tree/main/skills/scientific-visualization) `+ Python/SVG renderer`, [`nature-figure`](https://github.com/Yuan1z0825/nature-skills/tree/main/skills/nature-figure) `+ Python/SVG renderer`, `plot-code-python`, `schematic-svg`, `graphical-abstract-svg` |
| 调研报告 / Research report | 已支持 | 报告预览、sources.json、evidence table、claim checks、limitations、AI 评审 | `baseline-research-report`, `source-first-research`, `analyst-style-report`, `evidence-table-report`, `company-research-report`, [`user-research-cookiy`](https://github.com/cookiy-ai/user-research-skill) `+ report package` |
| 图片生成 / 生图比较 | 规划中 | 图片预览、文件链接、候选说明 | 暂不放固定候选；未来支持 image-generation pipelines |
| 网页 / HTML 制作比较 | 规划中 | 页面链接、截图预览、候选说明 | 暂不放固定候选；未来支持 web/HTML artifact pipelines |

## 四种工作模式

### 1. Text comparison

适合学术润色、自然化改写、审稿回复、投稿材料、PPT 方案/大纲等文本产物。

```bash
python3 scripts/compare.py \
  --input /tmp/forkprobe-input.txt \
  --skill baseline \
  --skill writing-anti-ai \
  --skill humanizer-zh \
  --skill remove-ai-flavor-writing-skill \
  --judge \
  --output /tmp/forkprobe-report.html
```

### 2. PPTX artifact comparison

如果用户目标是“做一个 PPT”或“生成 PPTX”，ForkProbe 会倾向比较成品生成 pipeline，而不是只比较文字大纲。策略 skill 必须搭配 `presentations` 或 `pptx` 这类生成器，完整 pipeline 才进入成品对比。

典型 shortlist：

- `baseline + presentations`
- `academic-pptx-skill + presentations`
- `nature-paper2ppt + presentations`
- `ppt-master`
- `md-slides`

生成每条 pipeline 的 PPTX 后，用 artifact report 展示文件链接、关键页预览和 AI 评审：

```bash
python3 scripts/render_artifact_report.py \
  --manifest /tmp/forkprobe-ppt-artifacts.json \
  --output /tmp/forkprobe-ppt-report.html
```

### 3. Figure artifact comparison

如果目标是论文作图、科研绘图、机制图、数据图或 graphical abstract，ForkProbe 会比较 figure 生成 pipeline。每条候选路径会生成一个 figure package，用 report 展示预览、源文件、caption 和 QA。

```bash
python3 scripts/figure_artifact.py \
  --input /tmp/forkprobe-figure-task.txt \
  --pipeline baseline-python-figure \
  --pipeline nature-figure-python \
  --pipeline plot-code-python \
  --skill-source 'https://github.com/K-Dense-AI/scientific-agent-skills#skills/scientific-visualization' \
  --run \
  --judge \
  --render-report \
  --report-output /tmp/forkprobe-figure-report.html
```

推荐产物包括 `preview.png`、`figure.svg`、`figure.pdf` 或 `figure.tiff`、源代码或矢量源文件、`caption.md` 和 `qa.md`。

### 4. Research report artifact comparison

如果目标是市场调研、公司调研、竞品分析、用户研究、文献综述或投研报告，ForkProbe 会比较 research report pipeline。每条候选路径会生成一个 research package，用 report 展示报告预览、来源、证据表、claim checks、limitations 和 AI 评审。

第一步必须先推荐候选，并等待用户确认：

```bash
python3 scripts/recommend.py --input /tmp/forkprobe-research-task.txt
```

确认候选后再运行 research artifact pipeline：

```bash
python3 scripts/research_artifact.py \
  --input /tmp/forkprobe-research-task.txt \
  --pipeline baseline-research-report \
  --pipeline source-first-research \
  --pipeline analyst-style-report \
  --pipeline evidence-table-report \
  --confirmed \
  --run \
  --judge \
  --render-report \
  --report-output /tmp/forkprobe-research-report.html
```

推荐产物包括 `candidate-report.md`、`candidate-report.html`、`sources.json`、`evidence-table.md`、`claim-checks.md`、`limitations.md` 和 `summary.md`。

## 支持的 Agent 工作流

- Claude Code / Claude 风格 skill 会话
- Codex 原生执行路径，并在失败时 fallback 到 OpenAI API
- OpenClaw、WorkBuddy、OpenCode 等自然语言 Agent 工作流
- “做一个 PPT”、“生成论文 figure”和“生成调研报告”这类成品生成任务的 artifact comparison

## 安装

将本项目复制到你的 Agent skill 目录即可。

Claude Code：

```bash
cp -r forkprobe ~/.claude/skills/
```

Codex / 本地 Agent skill 目录：

```bash
cp -r forkprobe ~/.agents/skills/
```

安装核心依赖：

```bash
pip3 install jinja2
```

Codex App / Codex CLI 路径会优先使用本地 `codex exec`，继承你的 Codex 登录和模型配置，不需要 `OPENAI_API_KEY`。

如果要走 Claude SDK 或 API fallback，可选安装：

```bash
pip3 install claude-agent-sdk
pip3 install anthropic openai
```

其中 `openai` SDK 和 `OPENAI_API_KEY` 只用于 Codex native CLI 不可用或被关闭时的 OpenAI API fallback。

## 快速开始

创建输入文件：

```bash
echo "请润色这段文字，并保留原意。" > /tmp/forkprobe-input.txt
```

先让 ForkProbe 推荐候选：

```bash
python3 scripts/recommend.py --input /tmp/forkprobe-input.txt
```

确认候选后运行一次本地文本对比：

```bash
python3 scripts/compare.py \
  --input /tmp/forkprobe-input.txt \
  --skill baseline \
  --skill writing-anti-ai \
  --skill humanizer-zh \
  --skill remove-ai-flavor-writing-skill \
  --judge \
  --output /tmp/forkprobe-report.html
```

打开 report：

```bash
open /tmp/forkprobe-report.html
```

## BYO、GitHub discovery 与 local-only

在正式对比前，`scripts/recommend.py` 可以先推荐候选。默认情况下，它会合并本地 curated 候选和 GitHub / 网络发现结果。网络搜索只使用经过清洗的任务信号，不会直接拿你的原始文档做搜索词。

```bash
python3 scripts/recommend.py --input /tmp/forkprobe-input.txt
```

如果只想使用本地候选：

```bash
python3 scripts/recommend.py --input /tmp/forkprobe-input.txt --local-only
```

BYO skill 支持本地路径、GitHub URL、`repo#subdir` 和 raw `SKILL.md` URL，例如：

```text
https://github.com/Yuan1z0825/nature-skills#skills/nature-polishing
```

## Report、winner 与 handoff

ForkProbe 的核心产物是本地 HTML report。文本模式展示每一路完整输出、耗时、token 估算和 AI 评审；artifact 模式展示 PPTX 或 figure package 的文件链接、预览、候选说明、caption、QA 和评审建议。

当用户在 report 中选择 winner 后，ForkProbe 会记录本地 verdict，并生成 continuation handoff。当前 Agent 可以沿用 winner 的风格、结构或文件产物继续完成正式任务。

如果目标是市场调研、公司调研、竞品分析、用户研究、文献综述或投研报告，forkprobe 会比较 research report pipeline。注意：这里必须先用推荐器展示候选并等待用户确认，不能直接运行 `research_artifact.py --run`。

```bash
python3 scripts/recommend.py --input /tmp/forkprobe-research-task.txt
```

确认候选后，每条候选路径会生成一个 research package，用 report 展示报告预览、来源、证据表、claim checks、limitations 和 AI 评审：

```bash
python3 scripts/research_artifact.py \
  --input /tmp/forkprobe-research-task.txt \
  --pipeline baseline-research-report \
  --pipeline source-first-research \
  --pipeline analyst-style-report \
  --pipeline evidence-table-report \
  --confirmed \
  --run \
  --judge \
  --render-report \
  --report-output /tmp/forkprobe-research-report.html
```

推荐产物包括 `candidate-report.md`、`candidate-report.html`、`sources.json`、`evidence-table.md`、`claim-checks.md`、`limitations.md` 和 `summary.md`。

## 隐私

- 任务内容保留在本地 report 和本地日志里。
- GitHub / 网络发现只使用清洗后的任务信号，不直接使用原始文档。
- 本地 verdict 日志只记录 winner、可选理由、report 路径和 continuation handoff。
- 如果不想联网，可以使用 `--local-only`，或明确说“只要本地候选”。
- 如果不想启动本地 verdict-capture server，可以使用 `--no-server`。
- 本地回写 token、CORS、远程 fetch 和命令执行说明见 [SECURITY.md](./SECURITY.md)。

## 测试

Smoke tests：

```bash
python3 tests/test_smoke.py
```

Integration tests 需要真实模型/API 访问：

```bash
FORKPROBE_RUN_INTEGRATION=1 python3 tests/test_integration.py
```

## 项目结构

```text
docs/       GitHub Pages 发布页和截图
scripts/    对比、推荐、报告和 verdict 工具
templates/  HTML report 模板
catalog/    curated skill catalog
tests/      smoke / integration tests
SKILL.md    Agent skill 指令
```

## License

MIT，见 [LICENSE](./LICENSE)。
