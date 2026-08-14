import z from '@deepseek-ai/schemastery';
import { defineTool } from '@deepseek-ai/dsh-tools';
import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { dirname, isAbsolute, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
export const name = 'forkprobe-dsh';
export const inject = ['tools', 'subagents'];
const PACKAGE_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const TOOL_NAMES = ['forkprobe_compare', 'forkprobe_resume'];
export const Config = z.object({
    provider: z.string().default('spawn'),
    pythonExecutable: z.string().default('python3'),
    maxCandidates: z.natural().min(2).max(8).default(5),
    verdictTimeoutSeconds: z.natural().min(30).max(7200).default(1800),
    maxDepth: z.natural().min(1).max(8).default(3),
});
function workspacePath(parent) {
    return parent.session.header.cwd || process.cwd();
}
function resolveWorkspaceFile(workspace, requested, fallback) {
    const candidate = resolve(workspace, requested?.trim() || fallback);
    const rel = relative(workspace, candidate);
    if (rel.startsWith('..') || isAbsolute(rel)) {
        throw new Error('ForkProbe output paths must stay inside the active DSH workspace');
    }
    return candidate;
}
function textFromBlocks(blocks) {
    return blocks
        .filter((block) => block.type === 'text')
        .map(block => block.text)
        .join('');
}
function candidatePrompt(task, skill) {
    return [
        'You are one isolated ForkProbe candidate inside DeepSeek Harness.',
        'Complete the original task using only the candidate instructions below.',
        'Do not compare candidates, call ForkProbe, modify files, or ask follow-up questions.',
        'Return only the final answer to the original task.',
        '',
        `Candidate id: ${skill.id}`,
        '',
        '## Candidate instructions',
        skill.system_prompt,
        '',
        '## Original task',
        task,
    ].join('\n');
}
function truncate(value, limit) {
    if (value.length <= limit)
        return value;
    const half = Math.floor(limit / 2);
    return `${value.slice(0, half)}\n\n[... truncated ${value.length - limit} chars ...]\n\n${value.slice(-half)}`;
}
function judgePrompt(task, results, rubric) {
    const candidates = results.map(result => [
        `## Candidate: ${result.id}`,
        `Name: ${result.name}`,
        result.error ? `[ERROR] ${result.error}` : truncate(result.output, 5000),
    ].join('\n')).join('\n\n---\n\n');
    return [
        'You are ForkProbe\'s impartial comparison judge.',
        'Compare candidates for fidelity, correctness, specificity, usefulness, clarity, and natural tone.',
        'Use a 0-10 score for every candidate. Return JSON only with this exact shape:',
        '{"winner_skill_id":"candidate id, __tie__, or __none__","verdict_type":"pick | tie | none","confidence":0.0,"summary":"one sentence","reasoning":"2-5 sentences","scores":{"candidate_id":{"score":0,"note":"short note"}}}',
        rubric?.trim() ? `Additional rubric: ${rubric.trim()}` : '',
        '',
        '## Original task',
        truncate(task, 4000),
        '',
        '## Candidates',
        candidates,
    ].filter(Boolean).join('\n');
}
async function runProcess(command, args, cwd, signal) {
    return new Promise((resolvePromise, reject) => {
        const child = spawn(command, args, {
            cwd,
            env: process.env,
            shell: false,
            stdio: ['ignore', 'pipe', 'pipe'],
        });
        let stdout = '';
        let stderr = '';
        const abort = () => child.kill('SIGTERM');
        signal.addEventListener('abort', abort, { once: true });
        child.stdout.setEncoding('utf8');
        child.stderr.setEncoding('utf8');
        child.stdout.on('data', chunk => { stdout += String(chunk); });
        child.stderr.on('data', chunk => { stderr += String(chunk); });
        child.on('error', reject);
        child.on('close', code => {
            signal.removeEventListener('abort', abort);
            if (signal.aborted) {
                reject(new Error('ForkProbe operation was cancelled'));
            }
            else if (code === 0) {
                resolvePromise(stdout.trim());
            }
            else {
                reject(new Error(`${command} exited ${String(code)}: ${stderr.trim() || stdout.trim()}`));
            }
        });
    });
}
async function startSubagent(ctx, config, parent, prompt, label, signal) {
    const provider = ctx.subagents.getProvider(config.provider);
    if (!provider)
        throw new Error(`ForkProbe subagent provider "${config.provider}" is not available`);
    if (!provider.capabilities.toolFilter) {
        throw new Error(`ForkProbe subagent provider "${config.provider}" must support tool filtering`);
    }
    const startedAt = performance.now();
    let run;
    try {
        run = await ctx.subagents.start(config.provider, {
            label,
            prompt: [{ type: 'text', text: prompt }],
            parent,
            signal,
            toolFilter: { allow: [] },
            ...(provider.capabilities.depthLimit ? { maxDepth: config.maxDepth } : {}),
        });
        const result = await run.result;
        const output = textFromBlocks(result.output);
        const error = result.stopReason === 'completed'
            ? undefined
            : `native DSH subagent ended with ${result.stopReason}`;
        return { output, latencySeconds: (performance.now() - startedAt) / 1000, ...(error ? { error } : {}) };
    }
    catch (error) {
        return {
            output: '',
            latencySeconds: (performance.now() - startedAt) / 1000,
            error: error instanceof Error ? error.message : String(error),
        };
    }
    finally {
        if (run)
            await run.dispose().catch(() => undefined);
    }
}
async function readJson(path) {
    return JSON.parse(await readFile(path, 'utf8'));
}
async function findLatestVerdictLog(workspace) {
    const logsDir = resolve(workspace, 'forkprobe-logs');
    const entries = await readdir(logsDir, { withFileTypes: true });
    const candidates = entries
        .filter(entry => entry.isFile() && /^\d{4}-.*\.json$/.test(entry.name))
        .map(entry => resolve(logsDir, entry.name))
        .sort()
        .reverse();
    if (!candidates.length)
        throw new Error('no ForkProbe logs exist in this workspace');
    const latest = candidates[0];
    for (const candidate of candidates) {
        try {
            const log = await readJson(candidate);
            if (log.verdict?.winner)
                return candidate;
        }
        catch {
            // Ignore an incomplete or unrelated JSON file and keep scanning.
        }
    }
    return latest;
}
async function waitForVerdict(logPath, results, timeoutSeconds, signal) {
    const deadline = Date.now() + timeoutSeconds * 1000;
    while (Date.now() < deadline && !signal.aborted) {
        try {
            const log = await readJson(logPath);
            const verdict = log.verdict;
            if (verdict?.winner) {
                const winner = String(verdict.winner);
                const selected = results.find(result => result.id === winner);
                return {
                    status: 'selected',
                    reportPath: String(log.report_path || ''),
                    logPath,
                    winner,
                    winnerName: String(verdict.winner_name || selected?.name || winner),
                    verdictType: String(verdict.verdict_type || 'pick'),
                    handoffText: String(verdict.handoff_text || ''),
                    ...(selected ? { selectedOutput: selected.output } : {}),
                    message: selected
                        ? `The user selected ${selected.name}. Continue the original task with this selected output and Skill.`
                        : 'The user submitted a tie or none verdict. Follow the verdict instead of the AI judge.',
                };
            }
        }
        catch {
            // The log may be between atomic browser updates; retry briefly.
        }
        await new Promise(resolvePromise => setTimeout(resolvePromise, 500));
    }
    return undefined;
}
function responseSchema() {
    return {
        type: 'object',
        additionalProperties: false,
        properties: {
            status: { type: 'string', required: true },
            reportPath: { type: 'string' },
            logPath: { type: 'string' },
            winner: { type: 'string' },
            winnerName: { type: 'string' },
            verdictType: { type: 'string' },
            handoffText: { type: 'string' },
            selectedOutput: { type: 'string' },
            judgeWinner: { type: 'string' },
            message: { type: 'string', required: true },
        },
    };
}
function renderResponse(_args, value) {
    const lines = [value.message];
    if (value.reportPath)
        lines.push(`Report: ${value.reportPath}`);
    if (value.winnerName)
        lines.push(`User-selected winner: ${value.winnerName}`);
    if (value.handoffText)
        lines.push(`Continuation handoff: ${value.handoffText}`);
    if (value.selectedOutput)
        lines.push(`Selected output:\n${value.selectedOutput}`);
    return [{ type: 'text', text: lines.join('\n') }];
}
export function apply(ctx, config) {
    ctx.tools.register(defineTool({
        name: 'forkprobe_compare',
        description: 'After the user explicitly confirms a shortlist, run 2-5 text Skill candidates through native DeepSeek Harness subagents in parallel, open a local ForkProbe comparison report, and continue only from the user-selected winner. Never call this before showing the shortlist and receiving confirmation.',
        parameters: {
            task: { type: 'string', required: true, description: 'The complete original user task and source text.' },
            skills: {
                type: 'array',
                required: true,
                items: { type: 'string' },
                description: 'Confirmed Skill IDs, local paths, or HTTPS GitHub sources. Baseline is added automatically.',
            },
            confirmed: { type: 'boolean', required: true, description: 'Must be true only after the user confirmed this shortlist.' },
            task_type: { type: 'string', description: 'Privacy-safe task category for optional aggregate feedback.' },
            domain: { type: 'string', description: 'ForkProbe catalog domain; defaults to academic-writing.' },
            output_path: { type: 'string', description: 'Report path inside the active workspace.' },
            judge: { type: 'boolean', description: 'Run a native DSH judge after candidate completion; defaults to true.' },
            judge_rubric: { type: 'string', description: 'Optional extra judge rubric.' },
            wait_for_verdict: { type: 'boolean', description: 'Wait for the Report Continue button and return the selected output; defaults to true.' },
        },
        output: { schema: responseSchema(), render: renderResponse },
        isConcurrencySafe: () => false,
        async execute(args, exec) {
            const parent = exec.agent;
            if (!parent)
                throw new Error('forkprobe_compare requires a calling DSH Agent');
            if (args.confirmed !== true) {
                throw new Error('Show the candidate shortlist and obtain explicit user confirmation before calling forkprobe_compare');
            }
            if (args.skills.length < 1 || args.skills.length > config.maxCandidates) {
                throw new Error(`Provide 1-${config.maxCandidates} confirmed Skill references; baseline is added automatically`);
            }
            const workspace = workspacePath(parent);
            const runId = randomUUID().slice(0, 8);
            const workDir = resolve(workspace, '.forkprobe-dsh', runId);
            await mkdir(workDir, { recursive: true });
            const skillsPath = resolve(workDir, 'skills.json');
            const preparedPath = resolve(workDir, 'prepared.json');
            const manifestPath = resolve(workDir, 'native-results.json');
            const reportPath = resolveWorkspaceFile(workspace, args.output_path, `forkprobe-dsh-${new Date().toISOString().replace(/[:.]/g, '')}-report.html`);
            await writeFile(skillsPath, JSON.stringify(args.skills), 'utf8');
            try {
                await runProcess(config.pythonExecutable, [
                    resolve(PACKAGE_ROOT, 'scripts/prepare_native_compare.py'),
                    '--skills-json', skillsPath,
                    '--domain', args.domain?.trim() || 'academic-writing',
                    '--output', preparedPath,
                    '--max-candidates', String(config.maxCandidates),
                ], workspace, exec.signal);
                const prepared = await readJson(preparedPath);
                const skills = prepared.skills;
                if (!Array.isArray(skills) || skills.length < 2) {
                    throw new Error('ForkProbe preparation did not produce at least two distinct candidates');
                }
                const candidateStart = performance.now();
                const results = await Promise.all(skills.map(async (skill) => {
                    const run = await startSubagent(ctx, config, parent, candidatePrompt(args.task, skill), `ForkProbe: ${skill.name}`, exec.signal);
                    return {
                        ...skill,
                        output: run.output,
                        latency_seconds: run.latencySeconds,
                        ...(run.error ? { error: run.error } : {}),
                        token_count_method: 'estimated_visible_context',
                        provider_tokens_used: 0,
                    };
                }));
                let judge = { enabled: false };
                if (args.judge !== false) {
                    const judgeRun = await startSubagent(ctx, config, parent, judgePrompt(args.task, results, args.judge_rubric), 'ForkProbe AI judge', exec.signal);
                    judge = {
                        enabled: true,
                        output: judgeRun.output,
                        latency_seconds: judgeRun.latencySeconds,
                        ...(judgeRun.error ? { error: judgeRun.error } : {}),
                    };
                }
                await writeFile(manifestPath, JSON.stringify({
                    schema_version: 1,
                    task_input: args.task,
                    task_type: args.task_type?.trim() || 'text_general',
                    duration_seconds: (performance.now() - candidateStart) / 1000,
                    results: results.map(result => ({
                        skill_id: result.id,
                        skill_name: result.name,
                        skill_author: result.author,
                        skill_category: result.category,
                        system_prompt: result.system_prompt,
                        output: result.output,
                        latency_seconds: result.latency_seconds,
                        token_count_method: result.token_count_method,
                        provider_tokens_used: result.provider_tokens_used,
                        ...(result.error ? { error: result.error } : {}),
                    })),
                    judge,
                }), 'utf8');
                const finalizedText = await runProcess(config.pythonExecutable, [
                    resolve(PACKAGE_ROOT, 'scripts/finalize_native_compare.py'),
                    '--manifest', manifestPath,
                    '--output', reportPath,
                    '--workspace', workspace,
                    '--verdict-timeout', String(config.verdictTimeoutSeconds),
                ], workspace, exec.signal);
                const finalized = JSON.parse(finalizedText);
                const ready = {
                    status: 'ready',
                    reportPath: String(finalized.report_path || reportPath),
                    logPath: String(finalized.log_path || ''),
                    ...(finalized.judge_winner ? { judgeWinner: String(finalized.judge_winner) } : {}),
                    message: finalized.verdict_connected
                        ? 'ForkProbe opened the local report. The AI judge is advisory; wait for the user to click Continue with their chosen winner.'
                        : 'ForkProbe created the report, but the local verdict server was unavailable. Ask the user to review it, then call forkprobe_resume after they select a winner.',
                };
                if (args.wait_for_verdict === false || !finalized.verdict_connected || !ready.logPath)
                    return ready;
                const selected = await waitForVerdict(ready.logPath, results, config.verdictTimeoutSeconds, exec.signal);
                return selected || {
                    ...ready,
                    status: 'awaiting_verdict',
                    message: 'The report is ready, but no user verdict arrived before the local wait window ended. Do not use the AI judge as the user choice; call forkprobe_resume after selection.',
                };
            }
            finally {
                await rm(workDir, { recursive: true, force: true }).catch(() => undefined);
            }
        },
    }));
    ctx.tools.register(defineTool({
        name: 'forkprobe_resume',
        description: 'Read the latest submitted ForkProbe Report verdict in this workspace and return the user-selected Skill output so the DSH Agent can continue the original task.',
        parameters: {
            log_path: { type: 'string', description: 'Optional ForkProbe log path inside the active workspace.' },
        },
        output: { schema: responseSchema(), render: renderResponse },
        isConcurrencySafe: () => true,
        async execute(args, exec) {
            const parent = exec.agent;
            if (!parent)
                throw new Error('forkprobe_resume requires a calling DSH Agent');
            const workspace = workspacePath(parent);
            const logPath = args.log_path
                ? resolveWorkspaceFile(workspace, args.log_path, args.log_path)
                : await findLatestVerdictLog(workspace);
            try {
                const log = await readJson(logPath);
                const verdict = log.verdict;
                if (!verdict?.winner) {
                    return { status: 'no_verdict', logPath, message: 'No submitted ForkProbe verdict was found yet.' };
                }
                const bundlePath = String(log.local_result_bundle_path || '');
                const bundle = bundlePath ? await readJson(resolveWorkspaceFile(workspace, bundlePath, bundlePath)) : {};
                const results = Array.isArray(bundle.results) ? bundle.results : [];
                const winner = String(verdict.winner);
                const selected = results.find(result => String(result.skill_id) === winner);
                return {
                    status: 'selected',
                    reportPath: String(log.report_path || ''),
                    logPath,
                    winner,
                    winnerName: String(verdict.winner_name || selected?.skill_name || winner),
                    verdictType: String(verdict.verdict_type || 'pick'),
                    handoffText: String(verdict.handoff_text || ''),
                    ...(selected?.output ? { selectedOutput: String(selected.output) } : {}),
                    message: selected
                        ? `Recovered the user-selected winner ${String(selected.skill_name || winner)}. Continue with this output and Skill.`
                        : 'Recovered a tie or none verdict. Follow the user verdict and do not substitute the AI judge recommendation.',
                };
            }
            catch (error) {
                return {
                    status: 'no_verdict',
                    logPath,
                    message: `Could not recover a submitted verdict: ${error instanceof Error ? error.message : String(error)}`,
                };
            }
        },
    }));
}
export const __testing = {
    candidatePrompt,
    judgePrompt,
    resolveWorkspaceFile,
    findLatestVerdictLog,
    textFromBlocks,
    toolNames: TOOL_NAMES,
};
