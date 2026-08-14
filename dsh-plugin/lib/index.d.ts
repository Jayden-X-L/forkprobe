import type { Context } from '@deepseek-ai/cordis';
import z from '@deepseek-ai/schemastery';
import type { ContentBlock } from '@deepseek-ai/dsh-llm';
export declare const name = "forkprobe-dsh";
export declare const inject: string[];
export interface Config {
    provider: string;
    pythonExecutable: string;
    maxCandidates: number;
    verdictTimeoutSeconds: number;
    maxDepth: number;
}
export declare const Config: z<Config>;
interface PreparedSkill {
    id: string;
    name: string;
    author: string;
    category: string;
    source: string;
    system_prompt: string;
    fingerprint: string;
}
interface CandidateResult extends PreparedSkill {
    output: string;
    latency_seconds: number;
    error?: string;
    token_count_method: 'estimated_visible_context';
    provider_tokens_used: 0;
}
declare function resolveWorkspaceFile(workspace: string, requested: string | undefined, fallback: string): string;
declare function textFromBlocks(blocks: ContentBlock[]): string;
declare function candidatePrompt(task: string, skill: PreparedSkill): string;
declare function judgePrompt(task: string, results: CandidateResult[], rubric?: string): string;
declare function findLatestVerdictLog(workspace: string): Promise<string>;
export declare function apply(ctx: Context, config: Config): void;
export declare const __testing: {
    candidatePrompt: typeof candidatePrompt;
    judgePrompt: typeof judgePrompt;
    resolveWorkspaceFile: typeof resolveWorkspaceFile;
    findLatestVerdictLog: typeof findLatestVerdictLog;
    textFromBlocks: typeof textFromBlocks;
    toolNames: string[];
};
export {};
