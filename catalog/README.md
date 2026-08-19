# ForkProbe Candidate Catalog

**Product compatibility: ForkProbe v1.0**

This directory contains the static, curated candidate catalogs bundled with
ForkProbe. Catalog revisions are independent from the ForkProbe product
version: a catalog number records the release in which that candidate set was
last changed, while the current runner remains compatible with all catalogs
listed below.

| Catalog | Revision | Coverage |
|---|---:|---|
| `academic-writing.json` | v0.4 | Academic writing, polishing, rebuttal, and humanization candidates |
| `pptx-artifact-skills.json` | v0.1 | PPTX artifact candidates |
| `web-artifact-skills.json` | v0.5 | Webpage artifact candidates |
| `video-artifact-skills.json` | v0.6 | Product promo, motion video, and rough-cut candidates |

ForkProbe v0.7 added multi-source discovery beyond these static catalogs,
including local Skill scanning, EverMind Skill Hub, GitHub, and user-provided
sources. ForkProbe v0.8 adds the optional anonymous Winner feedback loop and
aggregate community selection statistics. ForkProbe v0.9 adds DeepSeek Harness
as an execution platform for the same candidate catalogs. ForkProbe v0.10 adds
the installable native `forkprobe-dsh` plugin, native DSH subagent fan-out, and
same-Agent continuation after Report selection. ForkProbe v1.0 formalizes that
v0.10 capability set as the stable product baseline. Those product changes do
not require renumbering an unchanged candidate catalog.
