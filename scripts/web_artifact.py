"""Run and compare finished webpage candidates.

Each candidate receives the same task, builds an isolated static website, and is
then normalized through the same browser screenshot and QA pass. The resulting
manifest is rendered with ForkProbe's shared artifact report.
"""
from __future__ import annotations

import argparse
import contextlib
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CATALOG_PATH = PROJECT_DIR / "catalog" / "web-artifact-skills.json"
DEFAULT_OUTPUT_ROOT = PROJECT_DIR / "outputs" / "web-runs"
FALSE_VALUES = {"0", "false", "no", "off"}
PRIMARY_ARTIFACTS = [
    "site/index.html",
    "desktop.png",
    "mobile.png",
    "qa.json",
    "source.zip",
    "README.md",
]


@dataclass(frozen=True)
class WebPipeline:
    id: str
    name: str
    role: str
    summary_zh: str
    summary_en: str
    pipeline_steps: list[str]
    best_for: list[str]
    expected_artifacts: list[str]
    qa_checks: list[str]
    skill_source: str = ""
    runnable: bool = True
    requires: list[str] | None = None


@dataclass(frozen=True)
class WebRunResult:
    pipeline_id: str
    output: str
    tokens_used: int
    latency_seconds: float
    error: str | None = None


def _load_catalog() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _pipeline_from_meta(meta: dict[str, Any]) -> WebPipeline:
    source = str(meta.get("source") or "")
    subdir = str(meta.get("subdir") or "").strip("/")
    if source and subdir:
        source = f"{source}#{subdir}"
    return WebPipeline(
        id=str(meta["id"]),
        name=str(meta["name"]),
        role=str(meta.get("role") or "generator"),
        summary_zh=str(meta.get("summary_zh") or ""),
        summary_en=str(meta.get("summary_en") or ""),
        pipeline_steps=list(meta.get("pipeline_steps") or []),
        best_for=list(meta.get("default_families") or ["general"]),
        expected_artifacts=list(PRIMARY_ARTIFACTS),
        qa_checks=[
            "page_loads",
            "desktop_screenshot",
            "mobile_screenshot",
            "responsive_viewport",
            "local_assets_resolve",
            "basic_accessibility",
        ],
        skill_source=source,
        runnable=bool(meta.get("runnable", True)),
        requires=list(meta.get("requires") or []),
    )


def build_pipeline_registry(skill_sources: list[str] | None = None) -> tuple[dict[str, WebPipeline], list[str]]:
    registry = {
        pipeline.id: pipeline
        for pipeline in (_pipeline_from_meta(meta) for meta in _load_catalog().get("skills", []))
    }
    dynamic_ids: list[str] = []
    for source in skill_sources or []:
        pipeline = pipeline_from_skill_source(source, set(registry))
        registry[pipeline.id] = pipeline
        dynamic_ids.append(pipeline.id)
    return registry, dynamic_ids


def _compact(text: str) -> str:
    return "".join(text.lower().split())


def detect_web_family(task_text: str) -> str:
    compact = _compact(task_text)
    if any(word in compact for word in ["dashboard", "admin", "analytics", "数据看板", "仪表盘", "管理后台", "控制台"]):
        return "dashboard"
    if any(word in compact for word in ["reportpage", "报告页", "数据报告", "研究报告网页"]):
        return "report"
    if any(word in compact for word in ["landingpage", "落地页", "着陆页", "官网", "产品首页", "营销页"]):
        return "landing"
    if any(word in compact for word in ["webapp", "saas", "工具", "表单", "编辑器", "工作台", "交互应用"]):
        return "app"
    return "general"


def default_pipeline_ids(web_family: str, max_candidates: int = 5) -> list[str]:
    ordered = {
        "landing": ["baseline-web", "anthropic-frontend-design", "baoyu-design-web", "ui-ux-pro-max-web", "html-anything-prototype"],
        "dashboard": ["baseline-web", "anthropic-web-artifacts", "ui-ux-pro-max-web", "garden-web-design-engineer", "baoyu-design-web"],
        "app": ["baseline-web", "anthropic-web-artifacts", "garden-web-design-engineer", "ui-ux-pro-max-web", "baoyu-design-web"],
        "report": ["baseline-web", "anthropic-web-artifacts", "garden-web-design-engineer", "baoyu-design-web", "ui-ux-pro-max-web"],
        "general": ["baseline-web", "anthropic-frontend-design", "garden-web-design-engineer", "baoyu-design-web", "ui-ux-pro-max-web"],
    }[web_family]
    return ordered[:max_candidates]


def _slugify(value: str, default: str = "web-run") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower()).strip("-")
    return cleaned or default


def split_skill_source(source: str) -> tuple[str, str | None]:
    base, marker, fragment = source.partition("#")
    if not marker:
        return source, None
    subdir = fragment.strip().strip("/")
    return base, subdir or None


def _label_from_skill_source(source: str) -> str:
    base, subdir = split_skill_source(source)
    if subdir:
        return subdir.rstrip("/").split("/")[-1] or "external-web-skill"
    if base.startswith(("http://", "https://")):
        return base.rstrip("/").split("/")[-1].removesuffix(".git") or "external-web-skill"
    return Path(base).expanduser().name or "external-web-skill"


def pipeline_from_skill_source(source: str, existing_ids: set[str] | None = None) -> WebPipeline:
    label = _label_from_skill_source(source)
    base_id = f"skill-{_slugify(label, 'external-web-skill')}"
    pipeline_id = base_id
    suffix = 2
    while pipeline_id in (existing_ids or set()):
        pipeline_id = f"{base_id}-{suffix}"
        suffix += 1
    return WebPipeline(
        id=pipeline_id,
        name=f"{label} + web renderer",
        role="external_web_skill",
        summary_zh=f"使用外部网页 skill `{label}` 生成可运行、可截图、可比较的网站成品。",
        summary_en=f"Uses the external web skill `{label}` to generate a runnable, screenshot-ready website artifact.",
        pipeline_steps=[source, "web-renderer", "browser-qa"],
        best_for=["landing", "dashboard", "app", "report", "general"],
        expected_artifacts=list(PRIMARY_ARTIFACTS),
        qa_checks=["page_loads", "desktop_screenshot", "mobile_screenshot", "responsive_viewport", "local_assets_resolve"],
        skill_source=source,
    )


def _relative(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _read_optional(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def _load_run_result(candidate_dir: Path) -> dict[str, Any]:
    try:
        return json.loads((candidate_dir / "run-result.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_pipeline_skill_prompt(pipeline: WebPipeline) -> str:
    if not pipeline.skill_source:
        return ""
    if os.environ.get("FORKPROBE_WEB_LOAD_SKILL_PROMPTS", "1").lower() in FALSE_VALUES:
        return ""
    try:
        sys.path.insert(0, str(SCRIPT_DIR))
        from skill_loader import load_skill

        source, subdir = split_skill_source(pipeline.skill_source)
        loaded = load_skill(skill_id=pipeline.id, source=source, subdir=subdir)
        return loaded.to_system_prompt()
    except Exception as exc:
        return (
            f"Could not load skill instructions from {pipeline.skill_source}: "
            f"{type(exc).__name__}: {exc}\n"
            "Continue with the built-in web instructions and mention this fallback in summary.md."
        )


def build_pipeline_instructions(task_input: str, pipeline: WebPipeline, candidate_dir: Path) -> str:
    artifact_dir = candidate_dir / "artifacts"
    steps = " -> ".join(pipeline.pipeline_steps)
    source_note = f"\nExternal skill source: `{pipeline.skill_source}`\n" if pipeline.skill_source else ""
    return f"""# {pipeline.name}

## Goal

Generate one complete webpage artifact for the same original task as every other ForkProbe candidate.

## Original Task

{task_input}

## Pipeline

{steps}
{source_note}

## Output Contract

Write all generated files under `{artifact_dir}`.

- The final static website entry must be `{artifact_dir / 'site' / 'index.html'}`.
- Put editable source files under `{artifact_dir / 'source'}`.
- The page must work from a local HTTP server without secrets or unpublished services.
- Keep required assets local. Do not depend on remote placeholder images, fonts, or CDN-only runtime code.
- Implement responsive desktop and mobile layouts.
- Make visible interactions functional: navigation, tabs, filters, buttons, forms, or menus should not be decorative dead ends.
- Include semantic HTML, keyboard-visible focus, useful alt text, and adequate contrast.
- Do not start or leave behind a development server, watcher, or other long-running process.
- Do not spend time on a separate browser-control pass; ForkProbe owns the shared screenshot and browser QA stage.
- Do not ask the user follow-up questions and do not modify files outside `{candidate_dir}`.
- Write `{candidate_dir / 'summary.md'}` with the design direction, implementation, interactions, generated files, and known limitations.

ForkProbe will create `desktop.png`, `mobile.png`, `qa.json`, and `source.zip` after generation. Do not fake these files.
"""


def build_candidate_run_prompt(task_input: str, pipeline: WebPipeline, candidate_dir: Path) -> str:
    skill_prompt = load_pipeline_skill_prompt(pipeline)
    skill_section = ""
    if skill_prompt:
        skill_section = f"""
## External Skill Instructions

Apply the following skill before implementing the website. Resolve any output-path conflict in favor of ForkProbe's output contract.

{skill_prompt}
"""
    return f"""You are running one isolated ForkProbe webpage candidate.

Generate the requested finished website. Do not compare candidates and do not stop at a plan, wireframe, prompt, or code snippet.

Hard requirements:
- Build the actual runnable website under `{candidate_dir / 'artifacts' / 'site'}`.
- Preserve editable source under `{candidate_dir / 'artifacts' / 'source'}`.
- Create `{candidate_dir / 'summary.md'}`.
- Do not launch a persistent server, watcher, or separate browser agent. ForkProbe handles preview capture after you exit.
- Do not ask follow-up questions.
- Stop after files are written and return a concise completion summary.

{skill_section}

{(candidate_dir / 'INSTRUCTIONS.md').read_text(encoding='utf-8')}

## Original task, repeated for convenience

{task_input}
"""


class _PageInspector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self.has_viewport = False
        self.html_lang = ""
        self.interactive_count = 0
        self.image_count = 0
        self.images_missing_alt = 0
        self.refs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        if tag == "html":
            self.html_lang = str(data.get("lang") or "")
        if tag == "title":
            self._in_title = True
        if tag == "meta" and str(data.get("name") or "").lower() == "viewport":
            self.has_viewport = True
        if tag in {"a", "button", "input", "select", "textarea", "details", "summary"}:
            self.interactive_count += 1
        if tag == "img":
            self.image_count += 1
            if "alt" not in data or not str(data.get("alt") or "").strip():
                self.images_missing_alt += 1
        ref = data.get("src") if tag in {"img", "script", "source", "video", "audio", "iframe"} else None
        if tag == "link":
            ref = data.get("href")
        if ref:
            self.refs.append(str(ref))

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def _missing_local_refs(site_dir: Path, refs: list[str]) -> list[str]:
    missing: list[str] = []
    for ref in refs:
        value = urllib.parse.urlsplit(ref).path
        if not value or ref.startswith(("http://", "https://", "data:", "mailto:", "tel:", "#", "//")):
            continue
        target = site_dir / value.lstrip("/")
        if not target.exists():
            missing.append(ref)
    return sorted(set(missing))


def _find_site_entry(artifact_dir: Path) -> Path | None:
    preferred = [
        artifact_dir / "site" / "index.html",
        artifact_dir / "index.html",
        artifact_dir / "dist" / "index.html",
        artifact_dir / "build" / "index.html",
    ]
    for path in preferred:
        if path.exists():
            return path
    candidates = sorted(artifact_dir.rglob("index.html"), key=lambda path: (len(path.parts), str(path)))
    return candidates[0] if candidates else None


def _normalize_site(artifact_dir: Path) -> Path | None:
    entry = _find_site_entry(artifact_dir)
    if not entry:
        return None
    site_dir = artifact_dir / "site"
    target = site_dir / "index.html"
    if entry.resolve() == target.resolve():
        return target
    source_root = entry.parent
    if site_dir.exists():
        shutil.rmtree(site_dir)
    shutil.copytree(source_root, site_dir)
    return target if target.exists() else None


def _chrome_path() -> str | None:
    candidates = [
        os.environ.get("FORKPROBE_CHROME_BIN"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *args: object) -> None:
        return


@contextlib.contextmanager
def _serve_directory(directory: Path):
    handler = functools.partial(_QuietHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/index.html"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _capture_screenshot(chrome: str, url: str, output_path: Path, width: int, height: int) -> tuple[bool, str]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    playwright_error = ""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=chrome,
                headless=True,
                args=["--disable-gpu", "--hide-scrollbars", "--no-first-run"],
            )
            page = browser.new_page(
                viewport={"width": width, "height": height},
                device_scale_factor=1,
            )
            page.goto(url, wait_until="load", timeout=20_000)
            page.wait_for_timeout(500)
            page.screenshot(path=str(output_path), animations="disabled")
            browser.close()
        if output_path.exists() and _png_dimensions(output_path) == (width, height):
            return True, ""
        playwright_error = "Playwright returned a screenshot with unexpected dimensions"
    except ImportError:
        pass
    except Exception as exc:
        playwright_error = f"Playwright {type(exc).__name__}: {exc}"

    with tempfile.TemporaryDirectory(prefix="forkprobe-web-chrome-") as profile:
        common = [
            chrome,
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-dev-shm-usage",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=3000",
            f"--user-data-dir={profile}",
            f"--window-size={width},{height}",
            "--force-device-scale-factor=1",
            f"--screenshot={output_path}",
            url,
        ]
        attempts = [[chrome, "--headless=new", *common[1:]], [chrome, "--headless", *common[1:]]]
        errors: list[str] = [playwright_error] if playwright_error else []
        for command in attempts:
            try:
                proc = subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except OSError as exc:
                errors.append(str(exc))
                continue
            deadline = time.time() + 45
            while time.time() < deadline:
                if output_path.exists() and output_path.stat().st_size > 100 and _png_dimensions(output_path):
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=3)
                    return True, ""
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
            stdout, stderr = proc.communicate()
            if output_path.exists() and output_path.stat().st_size > 100 and _png_dimensions(output_path):
                return True, ""
            errors.append((stderr or stdout or f"exit {proc.returncode}").strip()[-800:])
        return False, " | ".join(error for error in errors if error)


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        data = path.read_bytes()[:24]
    except OSError:
        return None
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _measure_browser_layout(chrome: str, url: str, width: int, height: int) -> tuple[dict[str, int] | None, str]:
    """Measure rendered overflow with Playwright when its Python package is available."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "Playwright is unavailable; rendered overflow was not measured"

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=chrome,
                headless=True,
                args=["--disable-gpu", "--hide-scrollbars", "--no-first-run"],
            )
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(url, wait_until="load", timeout=20_000)
            page.wait_for_timeout(500)
            metrics = page.evaluate(
                """() => ({
                    innerWidth: window.innerWidth,
                    scrollWidth: Math.max(
                        document.documentElement.scrollWidth,
                        document.body ? document.body.scrollWidth : 0
                    )
                })"""
            )
            browser.close()
        return {
            "inner_width": int(metrics.get("innerWidth") or width),
            "scroll_width": int(metrics.get("scrollWidth") or 0),
        }, ""
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def postprocess_candidate(candidate_dir: Path) -> dict[str, Any]:
    artifact_dir = candidate_dir / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    entry = _normalize_site(artifact_dir)
    screenshot_errors: list[str] = []
    desktop = artifact_dir / "desktop.png"
    mobile = artifact_dir / "mobile.png"
    mobile_layout: dict[str, int] | None = None
    mobile_layout_error = ""

    if entry and os.environ.get("FORKPROBE_WEB_SKIP_SCREENSHOTS", "0").lower() not in {"1", "true", "yes"}:
        chrome = _chrome_path()
        if chrome:
            with _serve_directory(entry.parent) as url:
                for path, width, height in [(desktop, 1440, 1000), (mobile, 390, 844)]:
                    ok, error = _capture_screenshot(chrome, url, path, width, height)
                    if not ok:
                        screenshot_errors.append(f"{path.name}: {error or 'capture failed'}")
                mobile_layout, mobile_layout_error = _measure_browser_layout(chrome, url, 390, 844)
        else:
            screenshot_errors.append("Chrome/Chromium executable not found")
            mobile_layout_error = "Chrome/Chromium executable not found"

    inspector = _PageInspector()
    html_text = ""
    parse_error = ""
    if entry:
        try:
            html_text = entry.read_text(encoding="utf-8")
            inspector.feed(html_text)
        except (OSError, UnicodeDecodeError) as exc:
            parse_error = str(exc)
    missing_refs = _missing_local_refs(entry.parent, inspector.refs) if entry else []
    css_text = ""
    if entry:
        for ref in inspector.refs:
            path_value = urllib.parse.unquote(urllib.parse.urlsplit(ref).path)
            if not path_value.lower().endswith(".css") or ref.startswith(("http://", "https://", "//")):
                continue
            css_text += "\n" + _read_optional(entry.parent / path_value.lstrip("/"))
    responsive_source = f"{html_text}\n{css_text}".lower()
    responsive_css = any(marker in responsive_source for marker in ["@media", "clamp(", "minmax(", "container-type"])
    mobile_overflow_free = bool(
        mobile_layout
        and mobile_layout.get("scroll_width", 0) <= mobile_layout.get("inner_width", 390) + 1
    )
    if mobile_layout:
        mobile_overflow_detail = (
            f"scrollWidth={mobile_layout.get('scroll_width')}px, "
            f"viewport={mobile_layout.get('inner_width')}px"
        )
    else:
        mobile_overflow_detail = mobile_layout_error or "rendered overflow was not measured"

    checks = {
        "page_loads": {"passed": bool(entry and html_text), "detail": str(entry or "No index.html found")},
        "document_title": {"passed": bool(inspector.title.strip()), "detail": inspector.title.strip() or "Missing <title>"},
        "responsive_viewport": {"passed": inspector.has_viewport, "detail": "viewport meta present" if inspector.has_viewport else "Missing viewport meta"},
        "responsive_css": {"passed": responsive_css, "detail": "responsive CSS marker found" if responsive_css else "No @media/clamp/minmax marker found"},
        "interactions": {"passed": inspector.interactive_count > 0, "detail": f"{inspector.interactive_count} interactive elements"},
        "local_assets_resolve": {"passed": not missing_refs, "detail": "all local refs resolve" if not missing_refs else f"missing: {', '.join(missing_refs[:8])}"},
        "basic_accessibility": {
            "passed": bool(inspector.html_lang) and inspector.images_missing_alt == 0,
            "detail": f"lang={inspector.html_lang or 'missing'}, images missing alt={inspector.images_missing_alt}",
        },
        "desktop_screenshot": {"passed": desktop.exists(), "detail": str(_png_dimensions(desktop) or "missing")},
        "mobile_screenshot": {"passed": mobile.exists(), "detail": str(_png_dimensions(mobile) or "missing")},
        "mobile_horizontal_overflow": {"passed": mobile_overflow_free, "detail": mobile_overflow_detail},
    }
    weights = {
        "page_loads": 20,
        "document_title": 8,
        "responsive_viewport": 10,
        "responsive_css": 5,
        "interactions": 10,
        "local_assets_resolve": 15,
        "basic_accessibility": 7,
        "desktop_screenshot": 10,
        "mobile_screenshot": 5,
        "mobile_horizontal_overflow": 10,
    }
    score = sum(weight for name, weight in weights.items() if checks[name]["passed"])
    qa = {
        "score": score,
        "checks": checks,
        "missing_local_references": missing_refs,
        "screenshot_errors": screenshot_errors,
        "browser_layout_error": mobile_layout_error,
        "parse_error": parse_error,
        "viewports": {"desktop": "1440x1000", "mobile": "390x844"},
    }
    (artifact_dir / "qa.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")

    if entry:
        source_dir = artifact_dir / "source"
        if not source_dir.exists() or not any(source_dir.rglob("*")):
            if source_dir.exists():
                shutil.rmtree(source_dir)
            shutil.copytree(entry.parent, source_dir)
        archive_base = artifact_dir / "source"
        shutil.make_archive(str(archive_base), "zip", root_dir=source_dir)
        readme = artifact_dir / "README.md"
        if not readme.exists():
            readme.write_text(
                "# Web artifact\n\nOpen `site/index.html` through a local HTTP server. "
                "`desktop.png` and `mobile.png` are ForkProbe previews; `qa.json` records shared checks.\n",
                encoding="utf-8",
            )
    return qa


def collect_candidate_artifacts(candidate_dir: Path, output_dir: Path) -> list[dict[str, Any]]:
    artifact_dir = candidate_dir / "artifacts"
    artifacts: list[dict[str, Any]] = []
    for label in PRIMARY_ARTIFACTS:
        path = artifact_dir / label
        if path.exists() and path.is_file():
            artifacts.append({
                "path": _relative(path, output_dir),
                "label": label,
                "kind": path.suffix.lstrip(".").upper() or "FILE",
            })
    return artifacts


def candidate_summary(pipeline: WebPipeline, candidate_dir: Path) -> str:
    summary = _read_optional(candidate_dir / "summary.md")
    return f"{pipeline.summary_zh}\n\n{summary}".strip()


def estimate_candidate_tokens(
    task_input: str,
    pipeline: WebPipeline,
    candidate_dir: Path,
    summary: str,
    run_result: dict[str, Any],
) -> int:
    sys.path.insert(0, str(SCRIPT_DIR))
    from compare import estimate_text_tokens

    prompt = _read_optional(candidate_dir / "RUN_PROMPT.md") or _read_optional(candidate_dir / "INSTRUCTIONS.md")
    text = "\n\n".join(part for part in [prompt, task_input, str(run_result.get("output") or ""), summary] if part)
    return estimate_text_tokens(text)


def _web_preview(candidate_dir: Path, output_dir: Path) -> dict[str, Any]:
    artifact_dir = candidate_dir / "artifacts"
    qa: dict[str, Any] = {}
    try:
        qa = json.loads((artifact_dir / "qa.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    values = {
        "page_path": artifact_dir / "site" / "index.html",
        "desktop_path": artifact_dir / "desktop.png",
        "mobile_path": artifact_dir / "mobile.png",
    }
    preview = {
        key: _relative(path, output_dir)
        for key, path in values.items()
        if path.exists()
    }
    if qa or preview:
        preview["qa_score"] = int(qa.get("score") or 0)
    return preview


def build_manifest(
    task_input: str,
    output_dir: Path,
    pipeline_ids: list[str],
    web_family: str,
    pipeline_registry: dict[str, WebPipeline],
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for pipeline_id in pipeline_ids:
        pipeline = pipeline_registry[pipeline_id]
        candidate_dir = output_dir / "candidates" / pipeline.id
        run_result = _load_run_result(candidate_dir)
        summary = candidate_summary(pipeline, candidate_dir)
        candidates.append({
            "id": pipeline.id,
            "name": pipeline.name,
            "category": "web-artifact",
            "summary": summary,
            "workdir": _relative(candidate_dir, output_dir),
            "pipeline_steps": list(pipeline.pipeline_steps),
            "skill_source": pipeline.skill_source,
            "expected_artifacts": list(pipeline.expected_artifacts),
            "qa_checks": list(pipeline.qa_checks),
            "artifacts": collect_candidate_artifacts(candidate_dir, output_dir),
            "web_preview": _web_preview(candidate_dir, output_dir),
            "tokens_used": int(run_result.get("tokens_used") or 0),
            "provider_tokens_used": int(run_result.get("tokens_used") or 0),
            "estimated_tokens_used": estimate_candidate_tokens(task_input, pipeline, candidate_dir, summary, run_result),
            "latency_seconds": float(run_result.get("latency_seconds") or 0.0),
            "error": run_result.get("error"),
        })
    return {
        "schema_version": "web-artifact-v0.1",
        "deliverable_type": "web_artifact",
        "web_family": web_family,
        "task_input_path": "task.md",
        "duration_seconds": max((candidate["latency_seconds"] for candidate in candidates), default=0.0),
        "artifact_contract": {
            "required_page": "site/index.html",
            "required_previews": ["desktop.png", "mobile.png"],
            "required_qa": "qa.json",
            "recommended_source": "source.zip",
        },
        "candidates": candidates,
    }


def _codex_cli_path() -> str | None:
    for candidate in [
        os.environ.get("FORKPROBE_CODEX_CLI"),
        os.environ.get("CODEX_CLI_PATH"),
        shutil.which("codex"),
        "/Applications/Codex.app/Contents/Resources/codex",
    ]:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _parse_codex_tokens(text: str) -> int:
    match = re.search(r"tokens used\s+([0-9][0-9,]*)", text, flags=re.IGNORECASE)
    return int(match.group(1).replace(",", "")) if match else 0


def _tail(text: str, limit: int = 1400) -> str:
    text = text.strip()
    return text if len(text) <= limit else "..." + text[-limit:]


def _has_site(candidate_dir: Path) -> bool:
    return bool(_find_site_entry(candidate_dir / "artifacts"))


def _write_run_result(candidate_dir: Path, result: WebRunResult) -> None:
    (candidate_dir / "run-result.json").write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    if result.output:
        (candidate_dir / "runner-output.md").write_text(result.output, encoding="utf-8")
    if result.error:
        (candidate_dir / "runner-error.txt").write_text(result.error, encoding="utf-8")


def run_candidate_codex(
    task_input: str,
    output_dir: Path,
    pipeline_id: str,
    timeout: int,
    pipeline_registry: dict[str, WebPipeline],
) -> WebRunResult:
    pipeline = pipeline_registry[pipeline_id]
    candidate_dir = output_dir / "candidates" / pipeline.id
    candidate_dir.mkdir(parents=True, exist_ok=True)
    (candidate_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    prompt = build_candidate_run_prompt(task_input, pipeline, candidate_dir)
    (candidate_dir / "RUN_PROMPT.md").write_text(prompt, encoding="utf-8")
    cli = _codex_cli_path()
    if not cli:
        result = WebRunResult(pipeline.id, "", 0, 0.0, "Codex CLI not found. Set FORKPROBE_CODEX_CLI or install Codex CLI.")
        _write_run_result(candidate_dir, result)
        return result

    t0 = time.time()
    output_path: Path | None = None
    output = ""
    tokens = 0
    error: str | None = None
    transcript_handle = None
    try:
        with tempfile.NamedTemporaryFile(prefix="forkprobe-web-codex-", suffix=".txt", delete=False) as handle:
            output_path = Path(handle.name)
        command = [
            cli, "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox",
            os.environ.get("FORKPROBE_WEB_SANDBOX", "workspace-write"),
            "--output-last-message", str(output_path), "-C", str(PROJECT_DIR), "--add-dir", str(output_dir),
        ]
        model = os.environ.get("FORKPROBE_MODEL_CODEX_NATIVE")
        if model:
            command.extend(["--model", model])
        effort = os.environ.get("FORKPROBE_CODEX_REASONING_EFFORT")
        if effort:
            command.extend(["-c", f'model_reasoning_effort="{effort}"'])
        command.append("-")
        transcript_handle = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=transcript_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if proc.stdin is not None:
            proc.stdin.write(prompt)
            proc.stdin.close()
            proc.stdin = None

        deadline = time.time() + timeout
        last_size = -1
        stable_polls = 0
        final_message_ready = False
        while time.time() < deadline:
            size = output_path.stat().st_size if output_path.exists() else 0
            if size > 0:
                if size == last_size:
                    stable_polls += 1
                else:
                    stable_polls = 0
                    last_size = size
                if stable_polls >= 4:
                    final_message_ready = True
                    break
            if proc.poll() is not None:
                break
            time.sleep(0.1)

        timed_out = proc.poll() is None and not final_message_ready
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        transcript_handle.flush()
        transcript_handle.seek(0)
        transcript = transcript_handle.read()
        output = output_path.read_text(encoding="utf-8").strip() if output_path.exists() else transcript.strip()
        tokens = _parse_codex_tokens(transcript)
        if timed_out:
            error = f"Codex CLI timeout after {timeout}s." + (" Partial webpage files are available." if _has_site(candidate_dir) else "")
        elif proc.returncode not in {0, -15} and not final_message_ready:
            error = f"Codex CLI exited {proc.returncode}: {_tail(transcript)}"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if transcript_handle is not None:
            transcript_handle.close()
        if output_path:
            output_path.unlink(missing_ok=True)

    qa = postprocess_candidate(candidate_dir)
    if not qa.get("checks", {}).get("page_loads", {}).get("passed"):
        post_error = "No runnable webpage entry was generated under artifacts/site/index.html."
        error = f"{error} {post_error}".strip() if error else post_error
    result = WebRunResult(pipeline.id, output, tokens, time.time() - t0, error)
    _write_run_result(candidate_dir, result)
    return result


def run_parallel(
    task_input: str,
    output_dir: Path,
    pipeline_ids: list[str],
    pipeline_registry: dict[str, WebPipeline],
    max_workers: int = 2,
    timeout: int = 900,
) -> list[WebRunResult]:
    max_workers = int(os.environ.get("FORKPROBE_WEB_MAX_WORKERS", str(max_workers)))
    results: list[WebRunResult] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(pipeline_ids))) as executor:
        futures = {
            executor.submit(run_candidate_codex, task_input, output_dir, pipeline_id, timeout, pipeline_registry): pipeline_id
            for pipeline_id in pipeline_ids
        }
        for future in as_completed(futures):
            pipeline_id = futures[future]
            candidate_dir = output_dir / "candidates" / pipeline_id
            try:
                result = future.result()
            except Exception as exc:
                result = WebRunResult(pipeline_id, "", 0, 0.0, f"{type(exc).__name__}: {exc}")
                _write_run_result(candidate_dir, result)
            status = "ok" if not result.error else ("partial" if _has_site(candidate_dir) else "error")
            print(f"[forkprobe] web pipeline {pipeline_id}: {status} ({result.latency_seconds:.1f}s)", file=sys.stderr)
            results.append(result)
    order = {pipeline_id: index for index, pipeline_id in enumerate(pipeline_ids)}
    return sorted(results, key=lambda result: order[result.pipeline_id])


def build_artifact_judge_results(manifest: dict[str, Any], output_dir: Path) -> list[Any]:
    sys.path.insert(0, str(SCRIPT_DIR))
    from compare import RunResult

    results = []
    for candidate in manifest.get("candidates", []):
        preview = candidate.get("web_preview") or {}
        qa_score = preview.get("qa_score", 0)
        qa: dict[str, Any] = {}
        qa_path = output_dir / str(candidate.get("workdir") or "") / "artifacts" / "qa.json"
        try:
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        check_lines = [
            f"- {name}: {'pass' if detail.get('passed') else 'fail'}; {detail.get('detail') or ''}"
            for name, detail in (qa.get("checks") or {}).items()
            if isinstance(detail, dict)
        ]
        artifact_lines = [
            f"- {artifact.get('label') or artifact.get('path') or 'artifact'} ({artifact.get('kind') or 'file'})"
            for artifact in candidate.get("artifacts") or []
        ]
        summary = str(candidate.get("summary") or "")
        if len(summary) > 2200:
            summary = summary[:2200] + "\n[summary truncated for judge]"
        output = (
            f"{summary}\n\n"
            f"## Shared browser QA\nScore: {qa_score}/100\n"
            + ("\n".join(check_lines) or "No QA checks available.")
            + "\n\n## Generated artifacts\n"
            + ("\n".join(artifact_lines) or "No generated artifacts.")
        )
        artifacts = candidate.get("artifacts") or []
        results.append(RunResult(
            skill_id=str(candidate.get("id")),
            skill_name=str(candidate.get("name")),
            skill_author=str(candidate.get("author") or ""),
            skill_category="web-artifact",
            output=output,
            tokens_used=int(candidate.get("tokens_used") or 0),
            latency_seconds=float(candidate.get("latency_seconds") or 0.0),
            estimated_tokens_used=int(candidate.get("estimated_tokens_used") or 0),
            provider_tokens_used=int(candidate.get("provider_tokens_used") or 0),
            error=None if artifacts else candidate.get("error"),
        ))
    return results


def _preferred_web_judge_model() -> str | None:
    explicit = os.environ.get("FORKPROBE_WEB_JUDGE_MODEL")
    if explicit is not None:
        return explicit.strip() or None
    cache_path = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "models_cache.json"
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(cache, dict):
        return None
    available = {str(model.get("slug") or "") for model in cache.get("models", []) if isinstance(model, dict)}
    return "gpt-5.3-codex-spark" if "gpt-5.3-codex-spark" in available else None


def run_artifact_judge(
    task_input: str,
    manifest: dict[str, Any],
    output_dir: Path,
    rubric: str | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    sys.path.insert(0, str(SCRIPT_DIR))
    from compare import run_judge

    rubric_text = rubric or (
        "Evaluate finished webpage candidates using only the candidate summaries, artifact lists, and shared browser-QA "
        "metadata included in the prompt. Do not call tools, open files, inspect images, or launch a browser; return the "
        "required JSON verdict immediately. Score requirement fidelity 30%, implementation and interaction completeness 20%, "
        "responsive evidence 15%, accessibility evidence 10%, local asset and runtime stability 15%, and delivery completeness 10%. "
        "Treat visual-quality claims in candidate-authored summaries as unverified; the human user compares the actual screenshots."
    )
    effort_key = "FORKPROBE_CODEX_REASONING_EFFORT"
    ignore_config_key = "FORKPROBE_CODEX_IGNORE_USER_CONFIG"
    model_key = "FORKPROBE_MODEL_CODEX_NATIVE"
    previous_effort = os.environ.get(effort_key)
    previous_ignore_config = os.environ.get(ignore_config_key)
    previous_model = os.environ.get(model_key)
    os.environ[effort_key] = os.environ.get("FORKPROBE_WEB_JUDGE_REASONING_EFFORT", "low")
    os.environ[ignore_config_key] = os.environ.get("FORKPROBE_WEB_JUDGE_IGNORE_USER_CONFIG", "1")
    judge_model = _preferred_web_judge_model()
    if judge_model:
        os.environ[model_key] = judge_model
    try:
        with contextlib.redirect_stdout(sys.stderr):
            judge = run_judge(task_input, build_artifact_judge_results(manifest, output_dir), rubric=rubric_text, timeout=timeout)
    finally:
        if previous_effort is None:
            os.environ.pop(effort_key, None)
        else:
            os.environ[effort_key] = previous_effort
        if previous_ignore_config is None:
            os.environ.pop(ignore_config_key, None)
        else:
            os.environ[ignore_config_key] = previous_ignore_config
        if previous_model is None:
            os.environ.pop(model_key, None)
        else:
            os.environ[model_key] = previous_model
    return asdict(judge)


def create_workspace(
    task_input: str,
    output_dir: Path,
    pipeline_ids: list[str] | None = None,
    skill_sources: list[str] | None = None,
    max_candidates: int = 5,
) -> dict[str, Any]:
    output_dir = output_dir.expanduser().resolve()
    web_family = detect_web_family(task_input)
    registry, dynamic_ids = build_pipeline_registry(skill_sources)
    selected_ids = list(pipeline_ids or default_pipeline_ids(web_family, max_candidates))
    selected_ids.extend(pipeline_id for pipeline_id in dynamic_ids if pipeline_id not in selected_ids)
    unknown = [pipeline_id for pipeline_id in selected_ids if pipeline_id not in registry]
    if unknown:
        raise KeyError(f"Unknown web pipeline(s): {', '.join(unknown)}")
    conditional = [pipeline_id for pipeline_id in selected_ids if not registry[pipeline_id].runnable]
    if conditional:
        details = "; ".join(
            f"{pipeline_id} requires {', '.join(registry[pipeline_id].requires or ['external tooling'])}"
            for pipeline_id in conditional
        )
        raise ValueError(f"Conditional web pipeline(s) cannot run in the default runner: {details}")

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "task.md").write_text(task_input, encoding="utf-8")
    for pipeline_id in selected_ids:
        pipeline = registry[pipeline_id]
        candidate_dir = output_dir / "candidates" / pipeline_id
        (candidate_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        (candidate_dir / "INSTRUCTIONS.md").write_text(build_pipeline_instructions(task_input, pipeline, candidate_dir), encoding="utf-8")
    manifest = build_manifest(task_input, output_dir, selected_ids, web_family, registry)
    manifest_path = output_dir / "artifact-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "output_dir": str(output_dir),
        "manifest_path": str(manifest_path),
        "web_family": web_family,
        "pipelines": selected_ids,
        "skill_sources": list(skill_sources or []),
        "manifest": manifest,
    }


def _read_task(args: argparse.Namespace) -> str:
    if args.input:
        return Path(args.input).expanduser().read_text(encoding="utf-8")
    if args.text:
        return args.text
    return sys.stdin.read()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run finished webpage artifact comparisons")
    parser.add_argument("--input", help="Path to task input text")
    parser.add_argument("--text", help="Task description text when --input is omitted")
    parser.add_argument("--output-dir", help="Workspace directory; defaults to outputs/web-runs/<timestamp>")
    parser.add_argument("--pipeline", action="append", default=[], help="Web pipeline id; repeat to override defaults")
    parser.add_argument("--skill-source", action="append", default=[], help="External web skill source; repeat for multiple")
    parser.add_argument("--max-candidates", type=int, default=5, help="Maximum default candidate pipelines")
    parser.add_argument("--run", action="store_true", help="Run candidates in parallel")
    parser.add_argument("--refresh-artifacts", action="store_true", help="Re-run shared screenshot, QA, and source packaging for existing candidate files")
    parser.add_argument("--confirmed", action="store_true", help="Acknowledge that the user confirmed the shortlist")
    parser.add_argument("--timeout", type=int, default=900, help="Seconds allowed for each candidate (default: 900)")
    parser.add_argument("--max-workers", type=int, default=2, help="Maximum concurrent candidate runs")
    parser.add_argument("--judge", action="store_true", help="Run AI judging after generation and browser QA")
    parser.add_argument("--judge-rubric", default=None, help="Optional extra AI judge rubric")
    parser.add_argument("--judge-timeout", type=int, default=120, help="Seconds allowed for AI judge")
    parser.add_argument("--render-report", action="store_true", help="Render the artifact comparison report")
    parser.add_argument("--report-output", default="web-artifact-report.html", help="Report path inside workspace or absolute path")
    parser.add_argument("--no-open", action="store_true", help="Do not auto-open the report")
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    args = parser.parse_args()

    task_input = _read_task(args)
    if not task_input.strip():
        raise SystemExit("Task input is empty.")
    if args.run and not args.confirmed:
        raise SystemExit(
            "Refusing to run web pipelines before candidate confirmation. First run "
            "`python3 scripts/recommend.py --input <input.txt>`, show the shortlist, then rerun with --confirmed."
        )

    timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else DEFAULT_OUTPUT_ROOT / f"{timestamp}-{_slugify(task_input[:48])}"
    result = create_workspace(task_input, output_dir, args.pipeline or None, args.skill_source, args.max_candidates)
    registry, _ = build_pipeline_registry(args.skill_source)
    if args.run:
        run_parallel(task_input, Path(result["output_dir"]), list(result["pipelines"]), registry, args.max_workers, args.timeout)
        result = create_workspace(task_input, Path(result["output_dir"]), list(result["pipelines"]), args.skill_source, args.max_candidates)
    elif args.refresh_artifacts:
        for pipeline_id in result["pipelines"]:
            postprocess_candidate(Path(result["output_dir"]) / "candidates" / pipeline_id)
        result = create_workspace(task_input, Path(result["output_dir"]), list(result["pipelines"]), args.skill_source, args.max_candidates)

    if args.judge:
        manifest_path = Path(result["manifest_path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["judge"] = run_artifact_judge(task_input, manifest, Path(result["output_dir"]), args.judge_rubric, args.judge_timeout)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        result["manifest"] = manifest

    if args.render_report or args.run:
        sys.path.insert(0, str(SCRIPT_DIR))
        from render_artifact_report import render_from_manifest

        report_path = Path(args.report_output)
        if not report_path.is_absolute():
            report_path = Path(result["output_dir"]) / report_path
        render_from_manifest(Path(result["manifest_path"]), report_path, auto_open=not args.no_open)
        result["report_path"] = str(report_path.resolve())

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[forkprobe] Web workspace: {result['output_dir']}")
        print(f"[forkprobe] Web family: {result['web_family']}")
        print(f"[forkprobe] Pipelines: {', '.join(result['pipelines'])}")
        print(f"[forkprobe] Manifest: {result['manifest_path']}")
        if result.get("report_path"):
            print(f"[forkprobe] Report: {result['report_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
