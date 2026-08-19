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
  ·
  <a href="#deepseek-harness-原生插件">安装 DSH 插件</a>
</p>

<p align="center">
  <img alt="MIT License" src="https://img.shields.io/badge/license-MIT-111827">
  <img alt="Version v1.1" src="https://img.shields.io/badge/version-v1.1-2563eb">
  <img alt="Local first reports" src="https://img.shields.io/badge/report-local--first-0f9f8f">
  <img alt="Agent skill selector" src="https://img.shields.io/badge/agent-skill%20selector-2563eb">
  <a href="https://github.com/deepseek-ai/deepseek-harness"><img alt="DeepSeek Harness supported" src="https://img.shields.io/badge/harness-DeepSeek-0f9f8f"></a>
  <a href="https://github.com/topics/dsh-plugin"><img alt="DSH plugin community" src="https://img.shields.io/badge/community-dsh--plugin-2563eb"></a>
  <a href="https://github.com/openai/codex"><img alt="Built with OpenAI Codex" src="https://img.shields.io/badge/built%20with-OpenAI%20Codex-111827"></a>
</p>

ForkProbe 是一个 AI Skill 选型与试跑工具。它会把同一个任务交给模型本身和多个候选 skill，并排试跑，生成本地 HTML report，让你看到真实输出之后再选择 winner。

**v1.1 新增图片提示词 / 风格方向比较：** ForkProbe 现在可以比较 image prompt / style pipelines。每条候选先生成 `prompt.md`、`style-card.md`、`composition.md`、`negative-prompt.md` 和 `render-notes.md`，不在 runner 内调用图片 API；在 Codex 且宿主具备图片生成能力时，可根据本地 `render-queue.json` 做可选渲染验证，其他 Agent 可用用户外部渲染后回填 `rendered.png`。

选定 winner 后，Report 的“继续”按钮会同时保存本地 handoff，并让 Agent 沿胜出 Skill 继续任务。用户可以在同一区域选择是否匿名分享本次 Skill 选择，为未来的社区推荐先验积累样本。

当网络上的 skill 越来越多时，问题不再是“有没有 skill”，而是“当前任务到底该用哪个 skill”。ForkProbe 的目标很直接：先把结果摊开，再让 Agent 沿着你选中的路径继续工作。

## 什么时候该用 ForkProbe

- 你不确定当前任务该用哪个 skill，想先看真实输出再决定。
- 你想比较 baseline 和多个 skill，而不是只相信 skill 的描述。
- 你的交付物是 PPTX、科研 figure package、调研报告、图片 prompt/style package、可运行网页或视频成片，需要看文件、预览和 QA。
- 你想从本机已安装 Skill、EverMind Skill Hub、GitHub 或 BYO 路径中找到候选，再做一次小规模试跑。
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

1. 从 curated 目录、本机已安装 Skill、EverMind Skill Hub、GitHub 和 BYO 路径中推荐少量候选 skill 或 artifact pipeline。
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
| 图片提示词 / 风格方向比较 | 已支持 | Prompt、风格卡、构图说明、负面约束、可选图片预览、候选说明 | `baseline-image-prompt`, `creative-director-prompt`, `style-system-prompt`, `prompt-as-code`, `reference-to-style`, `ecommerce-product-prompt`, `poster-key-visual-prompt`, `social-cover-prompt`, `ppt-visual-prompt` |
| 网页 / HTML 制作比较 | 已支持 | 可运行页面链接、桌面/移动端截图、QA、源码、AI 评审 | `baseline-web`, [`Anthropic frontend-design`](https://github.com/anthropics/skills/tree/main/skills/frontend-design), [`Hallmark`](https://github.com/Nutlope/hallmark), [`web-artifacts-builder`](https://github.com/anthropics/skills/tree/main/skills/web-artifacts-builder), [`ui-ux-pro-max`](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill), [`web-design-engineer`](https://github.com/ConardLi/garden-skills/tree/main/skills/web-design-engineer), [`baoyu-design`](https://github.com/JimLiu/baoyu-design) |
| 产品宣传片成品比较 | 已支持 | MP4 播放、封面、字幕、脚本、分镜、源码、媒体 QA、AI 评审 | `baseline-remotion-agent`, [`HyperFrames product-launch-video`](https://github.com/heygen-com/hyperframes), [`video-shotcraft`](https://github.com/Vincentwei1021/video-shotcraft) |
| 动效视频成品比较 | 已支持 | MP4 播放、动效规格、源码、时长/分辨率、媒体 QA、AI 评审 | `baseline-remotion-motion`, [`HyperFrames motion-graphics`](https://github.com/heygen-com/hyperframes), [`Remotion Bits`](https://github.com/av/remotion-bits) |
| 口播粗剪比较 | 已支持 | 粗剪 MP4、字幕、转写稿、剪辑清单/时间线、压缩时长、媒体 QA | [`auto-editor`](https://github.com/WyattBlue/auto-editor), [`video-editing-skill`](https://github.com/maxazure/video-editing-skill), [`video-use`](https://github.com/browser-use/video-use) `cut-only`, [`chengfeng-videocut`](https://github.com/Agentchengfeng/chengfeng-videocut-skills)（实验） |

## 七种工作模式

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

### 5. Image prompt/style artifact comparison

如果目标是比较图片 skill、生图提示词、视觉风格、风格卡、海报 KV、电商主图、小红书封面、PPT 配图或概念图，ForkProbe 会先比较 prompt/style pipeline，而不是直接接图片 API。每条候选生成一个 prompt package，并可选写出 Codex host render queue。

第一步先推荐候选：

```bash
python3 scripts/recommend.py --input /tmp/forkprobe-image-task.txt
```

确认后运行图片提示词/风格方向对比：

```bash
python3 scripts/image_prompt_artifact.py \
  --input /tmp/forkprobe-image-task.txt \
  --pipeline baseline-image-prompt \
  --pipeline creative-director-prompt \
  --pipeline style-system-prompt \
  --pipeline prompt-as-code \
  --confirmed \
  --run \
  --judge \
  --render-report \
  --report-output /tmp/forkprobe-image-prompt-report.html
```

推荐产物包括 `prompt.md`、`style-card.md`、`composition.md`、`negative-prompt.md`、`render-notes.md`、`summary.md`，可选 `prompt.json`、`reference-usage.md`、`render-request.json` 和 `rendered.png`。`--render-mode codex-host` 会写出 `render-queue.json`，由宿主 Codex Agent 自行调用图片能力；ForkProbe runner 本身不会要求 API key，也不会直接调用图片 API。

### 6. Web artifact comparison

如果目标是 Landing Page、官网、Dashboard、Web App、报告页或 HTML 成品，ForkProbe 会先推荐网页生成候选，等待确认后再并行生成完整可运行页面。所有候选统一使用 `1440x1000` 和 `390x844` 视口截图，并执行本地资源、响应式、交互与基础可访问性 QA。环境中安装 Python Playwright 时，还会用真实浏览器测量移动端横向溢出；不可用时 `qa.json` 会明确记录该项未测量，而不会伪报通过。

第一步先推荐候选：

```bash
python3 scripts/recommend.py --input /tmp/forkprobe-web-task.txt
```

确认后运行网页成品对比：

```bash
python3 scripts/web_artifact.py \
  --input /tmp/forkprobe-web-task.txt \
  --pipeline baseline-web \
  --pipeline anthropic-frontend-design \
  --pipeline hallmark-web \
  --pipeline baoyu-design-web \
  --confirmed \
  --run \
  --judge \
  --render-report \
  --report-output /tmp/forkprobe-web-report.html
```

每条候选输出 `site/index.html`、`desktop.png`、`mobile.png`、`qa.json`、`source.zip` 和候选说明。Report 可切换桌面/移动端预览并直接打开成品页面。

### 7. Video artifact comparison

视频模式严格按场景分组，不会把产品宣传片、动效视频和口播粗剪混在同一轮评分。第一步先推荐候选并等待确认：

```bash
python3 scripts/recommend.py --input /tmp/forkprobe-video-task.txt
```

产品宣传片或动效视频确认后直接运行对应候选。口播粗剪必须使用 `--asset` 给所有候选提供同一个原始视频：

```bash
python3 scripts/video_artifact.py \
  --input /tmp/forkprobe-video-task.txt \
  --asset /path/to/source-video.mp4 \
  --pipeline auto-editor \
  --pipeline maxazure-video-editing \
  --pipeline video-use-cut-only \
  --pipeline chengfeng-cut-talking-head \
  --confirmed \
  --run \
  --judge \
  --render-report \
  --report-output /tmp/forkprobe-video-report.html
```

每条候选必须生成 `video.mp4`。ForkProbe 会用 `ffprobe` 检查时长、分辨率、编码和音轨，用 `ffmpeg` 生成统一封面，并根据场景检查字幕、脚本/分镜、动效规格或转写稿/剪辑清单。Report 内可直接播放成片。

## 支持的 Agent 工作流

- Claude Code / Claude 风格 skill 会话
- Codex 原生执行路径，并在失败时 fallback 到 OpenAI API
- DeepSeek Harness 原生插件，支持文本候选、AI judge、Report 选择和同一 Agent 继续
- DeepSeek Harness headless 兼容路径，继续支持科研图、报告、网页和视频等文件型 Artifact runner
- OpenClaw、WorkBuddy、OpenCode 等自然语言 Agent 工作流
- “做一个 PPT”、“生成论文 figure”、“生成调研报告”、“比较图片提示词/风格方向”、“制作网页成品”和“比较视频成片”这类 artifact comparison

## 版本历史

| 版本 | 重点 |
|---|---|
| v1.1 | 新增图片提示词 / 风格方向比较：prompt package、可选 Codex host render queue、用户渲染回填和 image prompt catalog。 |
| v1.0 | 将 v0.10 能力定版为稳定产品基线：DeepSeek Harness 原生插件、Report 选择后同一 Agent 继续。 |
| v0.10 | 新增 `forkprobe-dsh` 原生 DeepSeek Harness 插件，支持 native subagent fan-out、AI judge 和 continuation handoff。 |
| v0.9 | 增加 DeepSeek Harness headless 执行路径，覆盖文本候选和文件型 Artifact runner。 |
| v0.8 | 增加可选匿名 Winner 反馈闭环和社区选择先验。 |
| v0.7 | 增加多来源候选发现：本机 Skill 扫描、EverMind Skill Hub、GitHub discovery 和 BYO source。 |
| v0.6 | 增加视频成品比较：产品宣传片、动效视频和口播粗剪。 |
| v0.5 | 增加网页 / HTML 成品比较：可运行页面、桌面/移动截图和 QA。 |
| v0.4 | 增加去 AI 味 / humanizer 写作候选矩阵。 |
| v0.3 | 增加市场调研 / research report artifact comparison。 |
| v0.2 | 增加论文作图 / 科研绘图 artifact comparison。 |
| v0.1 | 初始公开发布：文本 skill 推荐、baseline 对比、本地 HTML report、AI judge 和 continuation handoff。 |

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

### DeepSeek Harness 原生插件

将 ForkProbe 直接安装到 DSH `web` profile：

```bash
dsh plugin --profile web add "github:Jayden-X-L/forkprobe"
```

需要在 headless profile 使用时再安装一次：

```bash
dsh plugin --profile headless add "github:Jayden-X-L/forkprobe"
```

重启对应 profile 后，对 DSH 说：

```text
请使用 ForkProbe 先推荐几个适合这次改写的 Skill，等我确认后再用原生 DSH subagent 并行试跑，打开 Report 让我选择 Winner，并沿胜出结果继续。
```

插件提供两个工具：`forkprobe_compare` 负责确认后的并行试跑，`forkprobe_resume` 负责在等待窗口结束后恢复 Report 中的选择。`forkprobe_compare` 强制要求 `confirmed=true`，候选 subagent 不获得工具权限，因此不会递归调用 ForkProbe 或改动工作区。

### DeepSeek Harness artifact 兼容路径

科研图、调研报告、网页和视频等文件型任务仍可通过官方 headless profile 运行现有 Python runner。准备好 `DEEPSEEK_API_KEY` 后：

```bash
FORKPROBE_PLATFORM=deepseek_harness \
DEEPSEEK_API_KEY=your-key \
python3 scripts/compare.py --input /tmp/forkprobe-input.txt --skill baseline --judge --output /tmp/forkprobe-report.html
```

也可以在命令中使用 `--platform deepseek_harness`。ForkProbe 会优先使用 `FORKPROBE_DSH_CLI` 指定的命令，其次使用全局 `dsh`，最后通过官方 `npx @deepseek-ai/dsh` 入口运行。DeepSeek Harness 当前为 developer preview，建议固定已验证版本用于稳定生产任务。

安装核心依赖：

```bash
pip3 install jinja2
```

视频模式另外需要本机安装 `FFmpeg`，用于媒体探测、封面和统一 QA：

```bash
brew install ffmpeg
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

同一条任务通过旧的 headless 兼容路径运行：

```bash
DEEPSEEK_API_KEY=your-key python3 scripts/compare.py \
  --platform deepseek_harness \
  --input /tmp/forkprobe-input.txt \
  --skill baseline \
  --skill writing-anti-ai \
  --judge \
  --output /tmp/forkprobe-deepseek-report.html
```

新安装优先使用上面的 DSH 原生插件完成文本候选比较。科研绘图、调研报告、网页和视频 runner 同样接受 `--platform deepseek_harness`；Artifact runner 默认使用 `workspace-write`，可用 `FORKPROBE_DSH_PERMISSION_MODE` 覆盖。

## 多来源候选发现、BYO 与 local-only

在正式对比前，`scripts/recommend.py` 会先生成候选清单并等待确认。默认候选来源包括：

- ForkProbe curated 目录和 baseline。
- 自动扫描的本机已安装 Skill：`~/.codex/skills`、`~/.agents/skills`、`~/.claude/skills`、`~/.dsh/skills`，以及项目内 `.codex/skills`、`.agents/skills`、`.claude/skills`、`.dsh/skills`、`skills`。
- EverMind Skill Hub 官方开放 API。
- GitHub 已知候选与实时搜索。
- 用户显式提供的本地路径、GitHub URL、`repo#subdir` 或 raw `SKILL.md` URL。

ForkProbe 会按内容指纹和来源去重，再按场景匹配度排序。外部发现只使用经过清洗的任务信号，不会直接拿你的原始文档做搜索词，也不会自动安装或执行未经确认的候选。

```bash
python3 scripts/recommend.py --input /tmp/forkprobe-input.txt
```

如果只想使用本地候选：

```bash
python3 scripts/recommend.py --input /tmp/forkprobe-input.txt --local-only
```

也可以单独关闭某个来源或强制刷新远程缓存：

```bash
python3 scripts/recommend.py --input /tmp/forkprobe-input.txt --no-evermind
python3 scripts/recommend.py --input /tmp/forkprobe-input.txt --no-local-skills
python3 scripts/recommend.py --input /tmp/forkprobe-input.txt --refresh-sources
```

使用 `FORKPROBE_LOCAL_SKILL_ROOTS`（以系统路径分隔符连接多个目录）可以覆盖默认扫描目录；本地索引默认写入 `~/.forkprobe/index/local-skills.json`，EverMind 查询缓存默认写入 `~/.forkprobe/cache/evermind/`。

BYO skill 支持本地路径、GitHub URL、`repo#subdir` 和 raw `SKILL.md` URL，例如：

```text
https://github.com/Yuan1z0825/nature-skills#skills/nature-polishing
```

## Report、winner 与 handoff

ForkProbe 的核心产物是本地 HTML report。文本模式展示每一路完整输出、耗时、token 估算和 AI 评审；artifact 模式展示 PPTX、figure package、research package、网页或视频成品的文件链接、预览/播放、候选说明、QA 和评审建议。

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

## 匿名 Winner 分享（可选）

Report 选择 winner 后会显示：

```text
已选择：Hallmark

☑ 匿名分享 Skill 选择，帮助 ForkProbe 改进推荐
  仅上传任务类型、参与比较的 Skill 名称和最终选择

[返回比较]                  [使用 Hallmark 继续]
```

- 首次使用默认勾选；用户继续时的选择会保存在 `~/.forkprobe/config.json`，供后续 Report 使用。
- 勾选后只上传 `task_type`、`candidate_skill_names` 和 `final_choice`。协议还包含随机事件 ID 与版本号，用于幂等去重。
- 不上传任务原文、候选输出、文件、评价理由、本地路径或用户身份。
- 事件先写入 `~/.forkprobe/telemetry/outbox/`，网络失败不会阻止 winner 保存或 Agent 继续，后续运行会自动重试。
- 设置 `FORKPROBE_TELEMETRY=0` 可强制关闭匿名分享；也可以在 Report 中取消勾选。
- 默认发送到 ForkProbe 官方 Cloudflare Worker：`https://forkprobe-selection-telemetry.forkprobe-selection-telemetry.workers.dev/v1/selection-events`。可通过 `FORKPROBE_TELEMETRY_ENDPOINT` 改为自托管接收端；Worker + D1 实现在 [`services/telemetry-worker`](./services/telemetry-worker/README.md)。
- 部分网络可能无法访问 `workers.dev`；事件会继续留在本地 outbox，使用可访问的自托管域名覆盖接收端后会自动重试。
- 统计按任务类型聚合，至少达到 20 次有效选择后才通过公共统计 API 返回 Skill 胜率和两两胜率。

## 隐私

- 任务内容保留在本地 report 和本地日志里。
- GitHub 和 EverMind Skill Hub 只接收清洗后的场景词，不接收原始任务、文档或本地路径。
- 本地 Skill 扫描只读取 `SKILL.md` 元数据和说明，用于索引与匹配；不会自动安装或执行 Skill。
- 本地 verdict 日志只记录任务哈希、候选元数据、winner、可选理由、report 路径和 continuation handoff。
- 匿名 Winner 分享由 Report 中的复选框控制；即使开启，任务内容和产物仍留在本地。
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
dsh-plugin/ DeepSeek Harness 原生 Cordis 插件
scripts/    对比、推荐、报告和 verdict 工具
templates/  HTML report 模板
catalog/    curated skill 与 artifact pipeline catalog
tests/      smoke / integration tests
services/   可选的 Cloudflare Worker + D1 匿名聚合服务
package.json  DSH 社区安装入口与插件元数据
SKILL.md    Agent skill 指令
```

## 协作说明

ForkProbe 由 [Jayden-X-L](https://github.com/Jayden-X-L) 发起、设计和维护。[OpenAI Codex](https://github.com/openai/codex) 作为 AI 开发协作者，参与了部分方案梳理、代码实现、测试和文档维护；产品方向与最终决策由项目作者负责。

## License

MIT，见 [LICENSE](./LICENSE)。
