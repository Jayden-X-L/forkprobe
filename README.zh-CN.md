# forkprobe

> 别猜哪个 skill 有用，直接并排看结果。

[English README](./README.md) | [发布页](https://jayden-x-l.github.io/forkprobe/)

forkprobe 帮你在正式使用某个 AI skill 之前，先把几个候选 skill 的结果并排跑出来。它会推荐候选、并行执行、生成本地 HTML report、给出 AI 评审建议；你选择 winner 后，再用 continuation handoff 让当前 Agent 会话沿着选中的结果继续执行。

当网络上的 skill 百花齐放时，forkprobe 的目标很简单：**先知道哪一个真的帮得上忙。**

## 为什么需要 forkprobe

skill 的描述通常都很好听，但真实效果会受任务、语言、领域、模型和上下文影响。科研润色、办公写作、金融分析、PPT 规划、PPTX 生成这些任务里，选错 skill 往往会浪费时间。

forkprobe 把 skill 选择变成一个可观察的流程：

1. 根据当前任务推荐少量候选 skill。
2. 用同一份输入跑 baseline 和多个 skill。
3. 生成本地 HTML report。
4. 展示输出质量、耗时、token 估算和 AI 评审建议。
5. 由用户选择 winner。
6. 生成 continuation handoff，让 Agent 继续执行正式任务。

forkprobe 不是用来写 skill 的工具。它负责发现、比较和选择已有 skill。

## 发布页

产品发布页位于 `docs/index.html`，可直接用 GitHub Pages 托管：

```text
https://jayden-x-l.github.io/forkprobe/
```

## 自然触发

你不需要记命令。可以直接对 Agent 说：

```text
先帮我比较几个 skill，看看哪个更适合当前任务。
```

或者更明确一点：

```text
请用 forkprobe 推荐候选，等我确认后再并排执行并生成 report，让我选择 winner。
```

## 支持的 Agent 工作流

forkprobe 当前已经实现的执行路径包括：

- Claude Code / Claude 风格 skill 会话
- Codex 原生执行路径，并在失败时 fallback 到 OpenAI API

同时，它也适合 OpenClaw、WorkBuddy、OpenCode 等 Agent 平台的自然语言工作流：先推荐候选，再生成 report，最后通过 handoff 继续执行。

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

依赖：

```bash
pip3 install jinja2 anthropic openai
```

如果你要走 Claude SDK 执行路径，可选安装：

```bash
pip3 install claude-agent-sdk
```

## 快速开始

创建输入文件：

```bash
echo "请润色这段文字，并保留原意。" > /tmp/forkprobe-input.txt
```

运行一次本地对比：

```bash
python3 scripts/compare.py \
  --input /tmp/forkprobe-input.txt \
  --skill baseline \
  --skill writing-anti-ai \
  --judge \
  --output /tmp/forkprobe-report.html
```

打开 report：

```bash
open /tmp/forkprobe-report.html
```

## 候选 skill 推荐

在正式对比前，forkprobe 可以先推荐候选：

```bash
python3 scripts/recommend.py --input /tmp/forkprobe-input.txt
```

默认情况下，它会合并本地 curated 候选和 GitHub / 网络发现结果。网络搜索只使用经过清洗的任务信号，不会直接拿你的原始文档做搜索词。

如果只想使用本地候选：

```bash
python3 scripts/recommend.py --input /tmp/forkprobe-input.txt --local-only
```

## PPTX 成品对比

如果用户的目标是“做一个 PPT”或“生成 PPTX”，forkprobe 会倾向比较成品生成 pipeline，而不是只比较文字大纲。它可以发现策略 skill、生成器和完整 pipeline，并从生成文件渲染 artifact report：

```bash
python3 scripts/render_artifact_report.py \
  --manifest /tmp/forkprobe-ppt-artifacts.json \
  --output /tmp/forkprobe-ppt-report.html
```

## 隐私

- 任务内容保留在本地 report 和本地日志里。
- GitHub / 网络发现只使用清洗后的任务信号，不直接使用原始文档。
- 本地 verdict 日志只记录 winner、可选理由、report 路径和 continuation handoff。
- 如果不想联网，可以使用 `--local-only`，或明确说“只要本地候选”。

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
docs/       GitHub Pages 发布页
scripts/    对比、推荐、报告和 verdict 工具
templates/  HTML report 模板
catalog/    curated skill catalog
tests/      smoke / integration tests
SKILL.md    Agent skill 指令
```

## License

MIT，见 [LICENSE](./LICENSE)。
