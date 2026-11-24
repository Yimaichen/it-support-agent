/** Project-owned LF JSON transport. Pi owns the entire model/tool loop. */
import { existsSync, mkdirSync, chmodSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { createInterface } from 'node:readline';
import { randomUUID } from 'node:crypto';
import { Type } from '@sinclair/typebox';
import { EnvHttpProxyAgent } from 'undici';
import { streamSimple as openAIStream } from '@earendil-works/pi-ai/api/openai-completions';
import {
  createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager,
  SettingsManager, compact, type AgentSession, type ToolDefinition,
} from '@earendil-works/pi-coding-agent';

type Packet = Record<string, unknown>;
process.umask(0o077); // Covers interrupted runs as well as successful finalization.
let session: AgentSession | undefined;
let requestId = '';
let busy = false;
let lastReply = '';
let settled = false;
let fatal = '';
let attempts = 0;
let streamQueue: Promise<void> = Promise.resolve();
let replyIndex = 0;
let usage = { input_tokens: 0, output_tokens: 0, total_tokens: 0 };
const pending = new Map<string, { resolve: (result: Packet) => void; reject: (e: Error) => void }>();
const emit = (packet: Packet) => process.stdout.write(JSON.stringify({ version: 1, request_id: requestId, ...packet }) + '\n');

function rpc(name: string, args: unknown, signal?: AbortSignal, callId: string = randomUUID()): Promise<Packet> {
  return new Promise((resolveResult, reject) => {
    if (signal?.aborted) { reject(new Error('cancelled')); return; }
    const onAbort = () => { pending.delete(callId); reject(new Error('cancelled')); };
    pending.set(callId, {
      resolve: result => { signal?.removeEventListener('abort', onAbort); resolveResult(result); },
      reject,
    });
    signal?.addEventListener('abort', onAbort, { once: true });
    emit({ type: 'tool_request', call_id: callId, name, arguments: args });
  });
}

async function init(packet: Packet) {
  if (session) throw new Error('already_initialized');
  requestId = String(packet.request_id);
  const root = resolve(String(packet.root));
  const config = packet.config as Packet;
  mkdirSync(root, { recursive: true, mode: 0o700 });
  const cwd = resolve(root, 'workspace');
  mkdirSync(cwd, { recursive: true, mode: 0o700 });
  const modelRuntime = await ModelRuntime.create({
    authPath: resolve(root, 'unused-auth.json'), modelsPath: null,
    modelsStorePath: resolve(root, 'catalog'), refreshOnCreate: false, allowModelNetwork: false,
  });
  const proxy = new EnvHttpProxyAgent();
  const providerFetch: typeof globalThis.fetch = async (input, options) => {
    try { return await globalThis.fetch(input, { ...options, dispatcher: proxy } as RequestInit); }
    catch (error) {
      const code = (error as { cause?: { code?: string } }).cause?.code;
      emit({ type: 'progress', event: 'network_error', details: { code: code || 'unknown' } });
      throw error;
    }
  };
  modelRuntime.registerProvider('it-support', {
    api: 'openai-completions', baseUrl: String(config.base_url),
    streamSimple: (model, context, options) => openAIStream(model as Parameters<typeof openAIStream>[0], context, {
      ...options, fetch: providerFetch,
      onPayload: async (body, currentModel) => {
        const original = await options?.onPayload?.(body, currentModel);
        const payload = (original ?? body) as Packet;
        const allowed = await rpc('__before_model', {});
        if (allowed.ok === false) { fatal = 'run_budget'; session?.agent.abort(); throw new Error('run_budget'); }
        attempts += 1;
        emit({ type: 'progress', event: 'model_started', details: {} });
        payload.max_tokens = Number(config.max_output_tokens);
        delete payload.max_completion_tokens;
        payload.thinking = { type: String(config.thinking_mode) };
        if (config.thinking_mode === 'enabled') payload.reasoning_effort = config.reasoning_effort;
        return payload;
      },
    }),
    apiKey: String(config.api_key), authHeader: true,
    models: [{
      id: String(config.model), name: String(config.model), reasoning: false, input: ['text'],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: Number(config.context_window ?? 131072), maxTokens: Number(config.max_output_tokens),
      compat: { supportsStore: false, supportsDeveloperRole: false, supportsReasoningEffort: false },
    }],
  });
  const model = modelRuntime.getModel('it-support', String(config.model));
  if (!model) throw new Error('model_configuration');
  const settings = SettingsManager.inMemory({
    cacheWarming: 'off', compaction: { enabled: true },
    retry: { enabled: true, maxRetries: Number(config.max_additional_retries),
      provider: { maxRetries: 0, timeoutMs: Number(config.request_timeout_seconds) * 1000 } },
  });
  const schemas = packet.tools as Array<{ name: string; description: string; parameters: Packet }>;
  const customTools: ToolDefinition[] = schemas.map(spec => ({
    name: spec.name, label: spec.name, description: spec.description,
    parameters: Type.Unsafe(spec.parameters),
    execute: async (id, args, signal) => {
      // Publish preceding assistant text before a tool can present source content.
      await streamQueue;
      if (fatal) throw new Error(fatal);
      const result = await rpc(spec.name, args, signal, id);
      return { content: [{ type: 'text' as const, text: JSON.stringify(result) }], details: result,
        isError: result.ok === false };
    },
  }));
  const loader = new DefaultResourceLoader({
    cwd, agentDir: root, settingsManager: settings,
    noExtensions: true, noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true,
    disabledBuiltinExtensions: ['mcp', 'codemode', 'tool-search'],
    systemPrompt: String(packet.system_prompt),
    agentsFilesOverride: () => ({ agentsFiles: [] }),
    appendSystemPromptOverride: () => [],
    extensionFactories: [pi => {
      pi.on('session_before_compact', async event => {
        if (!session || !model) return { cancel: true };
        emit({ type: 'progress', event: 'compaction_started', details: {} });
        const result = await compact(event.preparation, model, String(config.api_key), undefined,
          undefined,
          event.signal, 'off', session.agent.streamFunction);
        const response = await rpc('__sanitize_message', { message: { role: 'assistant',
          content: [{ type: 'text', text: result.summary }], usage: result.usage } });
        if (!response.ok || !response.message) process.exit(70);
        const cleaned = response.message as { content: Array<{ text: string }> };
        emit({ type: 'progress', event: 'compaction_finished', details: {} });
        return { compaction: { ...result, summary: cleaned.content[0].text } };
      });
      pi.on('message_end', async event => {
        // Redact before Pi persistence and subscribers; do not review or rewrite meaning.
        const response = await rpc('__sanitize_message', { message: event.message });
        if (!response.ok || !response.message) process.exit(70);
        return { message: response.message as typeof event.message };
      });
    }],
  });
  await loader.reload();
  if (loader.getExtensions().errors.length) throw new Error('extension_configuration');
  const file = packet.session_file ? resolve(String(packet.session_file)) : undefined;
  if (file && (!file.startsWith(root + '/') || !existsSync(file))) throw new Error('session_unavailable');
  const manager = file ? SessionManager.open(file, dirname(file), cwd) : SessionManager.create(cwd, resolve(root, 'sessions'));
  const created = await createAgentSession({ cwd, agentDir: root, modelRuntime, model,
    thinkingLevel: 'off', resourceLoader: loader, settingsManager: settings,
    tools: schemas.map(t => t.name), customTools, sessionManager: manager });
  session = created.session;
  const active = session.getActiveToolNames().sort();
  if (JSON.stringify(active) !== JSON.stringify(schemas.map(t => t.name).sort())) throw new Error('tool_allowlist');
  const preview = (text: string, index = replyIndex, finished = false) => {
    // Complete lines only: never reveal an incomplete password/token assignment.
    streamQueue = streamQueue.then(async () => {
      const cleaned = await rpc('__sanitize_stream', { text });
      if (!cleaned.ok) throw new Error('stream_redaction');
      emit({ type: 'progress', event: finished ? 'reply_finished' : 'reply_snapshot',
        details: { text: cleaned.text, message_index: index } });
    }).catch(() => { fatal = 'stream_redaction'; session?.agent.abort(); });
  };
  session.subscribe(event => {
    // Publish completed redacted messages. Never leak partial secret assignments.
    if (event.type === 'message_end' && event.message.role === 'assistant') {
      const message = event.message;
      const text = message.content.filter(c => c.type === 'text').map(c => c.text).join('\n');
      if (text.trim() && message.content.some(c => c.type === 'toolCall')) {
        streamQueue = streamQueue.then(() => {
          emit({ type: 'progress', event: 'investigation_note', details: { text } });
        });
      } else if (text.trim()) {
        lastReply = text;
        const index = ++replyIndex;
        streamQueue = streamQueue.then(() => {
          emit({ type: 'progress', event: 'reply_started', details: { message_index: index } });
        });
        preview(text, index);
        preview(text, index, true);
      }
      if (message.usage) {
        usage.input_tokens += message.usage.input + message.usage.cacheRead + message.usage.cacheWrite;
        usage.output_tokens += message.usage.output;
        usage.total_tokens += message.usage.totalTokens;
      }
    }
    if (event.type === 'agent_settled') settled = true;
    if (event.type === 'tool_execution_start' || event.type === 'tool_execution_end') {
      emit({ type: 'progress', event: event.type === 'tool_execution_start' ? 'tool_started' : 'tool_finished',
        details: { tool: event.toolName, ...(event.type === 'tool_execution_end' ? { ok: !event.isError } : {}) } });
    }
  });
  emit({ type: 'ready', session_file: manager.getSessionFile(), active_tools: active,
    message_count: session.messages.length, compaction_enabled: true });
}

async function prompt(packet: Packet) {
  if (!session || busy) throw new Error('not_ready');
  busy = true; settled = false; lastReply = ''; fatal = ''; attempts = 0;
  replyIndex = 0; streamQueue = Promise.resolve();
  usage = { input_tokens: 0, output_tokens: 0, total_tokens: 0 };
  const submission = String(packet.submission_id);
  const manager = session.sessionManager;
  const prior = manager.getEntries().find(entry => entry.type === 'custom' && entry.customType === 'it-submission' &&
    (entry.data as Packet)?.submission_id === submission);
  if (prior) throw new Error('submission_already_in_pi');
  manager.appendCustomEntry('it-submission', { submission_id: submission });
  try {
    // Bounded selected-case recovery context survives switches and compaction.
    const selection = JSON.stringify(packet.context);
    const previous = [...session.messages].reverse().find(m =>
      m.role === 'custom' && m.customType === 'it-case-selection');
    if (!previous || previous.role !== 'custom' || previous.content !== selection) {
      await session.sendCustomMessage({ customType: 'it-case-selection', display: false,
        content: selection });
    }
    await session.prompt(String(packet.text));
    await streamQueue;
    if (fatal) throw new Error(fatal);
    if (!settled || !session.isIdle) throw new Error('not_settled');
    const last = [...session.messages].reverse().find(m => m.role === 'assistant');
    if (last?.role === 'assistant' && ['error', 'aborted', 'length'].includes(last.stopReason)) {
      throw new Error(last.stopReason === 'length' ? 'output_truncated' : 'provider_error');
    }
    if (!lastReply.trim()) throw new Error('empty_reply');
    const sessionFile = manager.getSessionFile();
    if (sessionFile) chmodSync(sessionFile, 0o600);
    emit({ type: 'final', reply: lastReply, settled, attempts, usage, session_file: sessionFile,
      message_count: session.messages.length });
  } finally { busy = false; }
}

const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on('line', line => {
  if (Buffer.byteLength(line) > 2_000_000) { process.exit(65); }
  let packet: Packet;
  try { packet = JSON.parse(line) as Packet; } catch { process.exit(65); }
  if (packet.version !== 1) { process.exit(65); }
  if (packet.type === 'tool_response') {
    const callId = String(packet.call_id);
    const waiter = pending.get(callId); pending.delete(callId);
    waiter?.resolve(packet.result as Packet); return;
  }
  if (packet.type === 'cancel') { session?.agent.abort(); return; }
  if (packet.type === 'close') { session?.dispose(); process.exit(0); }
  const task = packet.type === 'init' ? init(packet) : packet.type === 'prompt' ? prompt(packet) : Promise.reject(new Error('invalid_command'));
  task.catch(error => {
    const code = error instanceof Error ? error.message : '';
    const known = ['run_budget', 'stream_redaction', 'provider_error', 'output_truncated', 'empty_reply',
      'not_settled', 'submission_already_in_pi', 'session_unavailable', 'tool_allowlist', 'extension_configuration'];
    emit({ type: 'error', error: fatal || (known.includes(code) ? code : 'runtime_interrupted') });
  });
});
input.on('close', () => { session?.dispose(); process.exit(0); });
