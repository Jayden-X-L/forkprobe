import assert from 'node:assert/strict'
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { __testing } from '../lib/index.js'

test('candidate prompt is standalone and blocks recursive ForkProbe use', () => {
  const prompt = __testing.candidatePrompt('改写这段文字', {
    id: 'humanizer',
    name: 'Humanizer',
    author: 'test',
    category: 'writing',
    source: 'local',
    system_prompt: 'Keep the meaning.',
    fingerprint: 'x',
  })
  assert.match(prompt, /Do not compare candidates, call ForkProbe/)
  assert.match(prompt, /Keep the meaning/)
  assert.match(prompt, /改写这段文字/)
})

test('judge prompt uses the 0-10 contract', () => {
  const prompt = __testing.judgePrompt('task', [{
    id: 'a', name: 'A', author: '', category: '', source: '', system_prompt: '', fingerprint: '',
    output: 'answer', latency_seconds: 1, token_count_method: 'estimated_visible_context', provider_tokens_used: 0,
  }])
  assert.match(prompt, /0-10 score/)
  assert.match(prompt, /"score":0/)
})

test('output paths cannot escape the DSH workspace', () => {
  assert.equal(__testing.resolveWorkspaceFile('/tmp/work', 'reports/a.html', 'x'), '/tmp/work/reports/a.html')
  assert.throws(() => __testing.resolveWorkspaceFile('/tmp/work', '../outside.html', 'x'), /must stay inside/)
})

test('resume finds the newest submitted verdict without latest.json', async () => {
  const workspace = await mkdtemp(join(tmpdir(), 'forkprobe-dsh-test-'))
  try {
    const logs = join(workspace, 'forkprobe-logs')
    await mkdir(logs)
    await writeFile(join(logs, '2026-08-14T010000Z-old.json'), JSON.stringify({ verdict: null }))
    const selected = join(logs, '2026-08-14T020000Z-new.json')
    await writeFile(selected, JSON.stringify({ verdict: { winner: 'humanizer' } }))
    assert.equal(await __testing.findLatestVerdictLog(workspace), selected)
  } finally {
    await rm(workspace, { recursive: true, force: true })
  }
})
