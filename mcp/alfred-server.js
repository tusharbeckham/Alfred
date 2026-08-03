#!/usr/bin/env node
'use strict';
/*
 * Alfred MCP Server — a dependency-free Model Context Protocol server (JSON-RPC 2.0 over
 * stdio, newline-delimited). Exposes Alfred's own tools: memory access, eval results,
 * agent listing, and (confirmation-gated) automation triggers.
 *
 * Run: node mcp/alfred-server.js   (set ALFRED_ROOT to override the repo location)
 * Configured as the "alfred" server in .kiro/settings/mcp.json (Kiro) and .mcp.json (Claude Code).
 */
const fs = require('fs');
const path = require('path');
const readline = require('readline');
const { spawn, spawnSync } = require('child_process');

// Portable: $ALFRED_ROOT wins, else the repo this file lives in. A clone works
// from any path on any machine without editing the server.
const ROOT = process.env.ALFRED_ROOT || path.resolve(__dirname, '..');
const MEMORY = path.join(ROOT, 'memory');
const RESULTS = path.join(ROOT, 'evals', 'results');
const AGENTS = path.join(ROOT, '.kiro', 'agents');
const SCRIPTS = path.join(ROOT, 'scripts');
const WORKFLOWS = path.join(ROOT, 'workflows');

// ---- JSON-RPC plumbing ----------------------------------------------------
function send(msg) { process.stdout.write(JSON.stringify(msg) + '\n'); }
function ok(id, result) { send({ jsonrpc: '2.0', id, result }); }
function err(id, code, message) { send({ jsonrpc: '2.0', id, error: { code, message } }); }
function text(t) { return { content: [{ type: 'text', text: String(t) }] }; }

// ---- Safety helpers -------------------------------------------------------
function safeMemoryPath(name) {
  if (typeof name !== 'string' || !/^[\w.\-]+$/.test(name)) {
    throw new Error('Invalid memory file name (letters, digits, dot, dash, underscore only).');
  }
  const p = path.resolve(MEMORY, name);
  if (path.dirname(p) !== path.resolve(MEMORY)) throw new Error('Path escapes memory/ directory.');
  return p;
}

function safeName(name, label) {
  if (typeof name !== 'string' || !/^[\w.\-]+$/.test(name)) {
    throw new Error('Invalid ' + label + ' (letters, digits, dot, dash, underscore only).');
  }
  return name;
}

// Resolve a workflow spec argument to a path inside workflows/. Accepts a bare
// name ("feature" / "feature.json") only — never an arbitrary path.
function safeWorkflowPath(name) {
  safeName(name, 'workflow name');
  const file = name.endsWith('.json') ? name : name + '.json';
  const p = path.resolve(WORKFLOWS, file);
  if (path.dirname(p) !== path.resolve(WORKFLOWS)) throw new Error('Path escapes workflows/ directory.');
  return p;
}

// Run a repo Python script synchronously and return {code, out}. Dependency-free:
// uses child_process.spawnSync. Output is capped so a runaway script can't flood.
function runPython(scriptArgs, timeoutMs) {
  const py = process.env.ALFRED_PYTHON || 'python';
  const r = spawnSync(py, scriptArgs, {
    cwd: ROOT, encoding: 'utf8', timeout: timeoutMs || 120000,
    maxBuffer: 4 * 1024 * 1024
  });
  if (r.error) {
    if (r.error.code === 'ETIMEDOUT') return { code: 124, out: '(timed out)' };
    return { code: 127, out: String(r.error.message || r.error) };
  }
  const out = (r.stdout || '') + (r.stderr ? '\n' + r.stderr : '');
  return { code: r.status == null ? 1 : r.status, out: out.trim() || '(no output)' };
}

// ---- Tool definitions -----------------------------------------------------
const TOOLS = [
  {
    name: 'read_memory',
    description: "Read one of Alfred's memory files (e.g. decisions.md, learnings.md, todo.md, session-log.txt).",
    inputSchema: { type: 'object', properties: { file: { type: 'string', description: 'File name inside memory/.' } }, required: ['file'] }
  },
  {
    name: 'write_memory',
    description: "Append to (default) or overwrite an Alfred memory file. Use for logging decisions/learnings.",
    inputSchema: {
      type: 'object',
      properties: {
        file: { type: 'string', description: 'File name inside memory/.' },
        content: { type: 'string', description: 'Text to write.' },
        mode: { type: 'string', enum: ['append', 'overwrite'], description: 'Default append.' }
      },
      required: ['file', 'content']
    }
  },
  {
    name: 'query_eval_results',
    description: 'List recent eval result files and return the most recent summary from evals/results/.',
    inputSchema: { type: 'object', properties: {} }
  },
  {
    name: 'list_agents',
    description: "List Alfred's agents with their descriptions and models.",
    inputSchema: { type: 'object', properties: {} }
  },
  {
    name: 'trigger_train',
    description: 'Return the command to run the training loop. Set confirm=true to actually launch scripts/train.ps1 in the background.',
    inputSchema: { type: 'object', properties: { confirm: { type: 'boolean' } } }
  },
  {
    name: 'trigger_overnight',
    description: 'Return the command to run the overnight backlog. Set confirm=true to actually launch scripts/overnight-run.ps1 in the background.',
    inputSchema: { type: 'object', properties: { confirm: { type: 'boolean' } } }
  },
  {
    name: 'list_workflows',
    description: 'List the DAG workflow specs available in workflows/ (the pipelines the engine can run).',
    inputSchema: { type: 'object', properties: {} }
  },
  {
    name: 'plan_workflow',
    description: 'Preview a workflow: its parallel execution waves and stages. Read-only, spawns no agents.',
    inputSchema: {
      type: 'object',
      properties: { workflow: { type: 'string', description: 'Workflow name (e.g. "feature"), with or without .json.' } },
      required: ['workflow']
    }
  },
  {
    name: 'run_workflow',
    description: 'Run a workflow DAG. Without confirm=true this is a DRY RUN (spawns nothing, prints the plan). Set confirm=true to execute for real against real model backends — this can spend budget.',
    inputSchema: {
      type: 'object',
      properties: {
        workflow: { type: 'string', description: 'Workflow name (e.g. "feature"), with or without .json.' },
        task: { type: 'string', description: 'The objective/task text injected into the pipeline.' },
        backend: { type: 'string', enum: ['auto', 'claude', 'api', 'local', 'kiro', 'dry'], description: 'Model backend. Default auto.' },
        parallel: { type: 'integer', description: 'Max concurrent stages per wave (default 4; 1 = strictly sequential).' },
        budget: { type: 'number', description: 'Cap on stage executions for the run.' },
        confirm: { type: 'boolean', description: 'Set true to EXECUTE for real. Otherwise a dry run.' }
      },
      required: ['workflow', 'task']
    }
  },
  {
    name: 'workflow_runs',
    description: 'Show recent workflow run history with status and cost.',
    inputSchema: { type: 'object', properties: {} }
  },
  {
    name: 'run_agent',
    description: 'Run one Alfred agent on a task via Ultron. Without confirm=true returns a DRY-RUN preview of the assembled prompt. Set confirm=true to actually call the model backend.',
    inputSchema: {
      type: 'object',
      properties: {
        agent: { type: 'string', description: 'Agent name (e.g. "alfred-qa").' },
        task: { type: 'string', description: 'The task/question for the agent.' },
        backend: { type: 'string', enum: ['local', 'claude', 'api', 'kiro'], description: 'Model backend. Default local (free).' },
        confirm: { type: 'boolean', description: 'Set true to actually call the model. Otherwise a dry-run prompt preview.' }
      },
      required: ['agent', 'task']
    }
  },
  {
    name: 'recall_memory',
    description: "Semantic/keyword recall from Alfred's megamind memory (SQLite FTS). Returns the most relevant stored items.",
    inputSchema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'What to recall.' },
        k: { type: 'integer', description: 'How many results (default 5).' }
      },
      required: ['query']
    }
  },
  {
    name: 'doctor',
    description: 'Report which model backends (claude / api / local / kiro) are live and the engine health.',
    inputSchema: { type: 'object', properties: {} }
  }
];

// ---- Tool implementations -------------------------------------------------
function callTool(name, args) {
  args = args || {};
  switch (name) {
    case 'read_memory': {
      const p = safeMemoryPath(args.file);
      if (!fs.existsSync(p)) return text('(memory file not found: ' + args.file + ')');
      return text(fs.readFileSync(p, 'utf8'));
    }
    case 'write_memory': {
      const p = safeMemoryPath(args.file);
      const mode = args.mode === 'overwrite' ? 'overwrite' : 'append';
      const body = String(args.content == null ? '' : args.content);
      if (mode === 'overwrite') fs.writeFileSync(p, body, 'utf8');
      else fs.appendFileSync(p, (body.endsWith('\n') ? body : body + '\n'), 'utf8');
      return text('Wrote (' + mode + ') to memory/' + args.file);
    }
    case 'query_eval_results': {
      if (!fs.existsSync(RESULTS)) return text('(no evals/results/ yet)');
      const files = fs.readdirSync(RESULTS).filter(f => f.endsWith('.json')).sort();
      if (!files.length) return text('(no eval result files yet)');
      const latest = files[files.length - 1];
      const body = fs.readFileSync(path.join(RESULTS, latest), 'utf8');
      return text('Latest: ' + latest + '\nAll: ' + files.join(', ') + '\n\n' + body);
    }
    case 'list_agents': {
      if (!fs.existsSync(AGENTS)) return text('(no agents dir)');
      const out = fs.readdirSync(AGENTS).filter(f => f.endsWith('.json')).map(f => {
        try { const j = JSON.parse(fs.readFileSync(path.join(AGENTS, f), 'utf8'));
          return `- ${j.name} [${j.model || 'default'}] — ${j.description || ''}`; }
        catch { return `- ${f} (unparseable)`; }
      });
      return text(out.join('\n'));
    }
    case 'trigger_train':
      return launchScript('train.ps1', args.confirm);
    case 'trigger_overnight':
      return launchScript('overnight-run.ps1', args.confirm);
    case 'list_workflows': {
      if (!fs.existsSync(WORKFLOWS)) return text('(no workflows/ dir)');
      const specs = fs.readdirSync(WORKFLOWS).filter(f => f.endsWith('.json')).sort();
      if (!specs.length) return text('(no workflow specs yet)');
      const out = specs.map(f => {
        try {
          const j = JSON.parse(fs.readFileSync(path.join(WORKFLOWS, f), 'utf8'));
          const stages = Array.isArray(j.stages) ? j.stages.length : 0;
          return `- ${f.replace(/\.json$/, '')} — ${j.description || '(no description)'} (${stages} stages)`;
        } catch { return `- ${f} (unparseable)`; }
      });
      return text(out.join('\n'));
    }
    case 'plan_workflow': {
      const spec = safeWorkflowPath(args.workflow);
      const r = runPython([path.join(SCRIPTS, 'workflow.py'), 'plan', spec]);
      return text(r.out);
    }
    case 'workflow_runs': {
      const r = runPython([path.join(SCRIPTS, 'workflow.py'), 'runs']);
      return text(r.out);
    }
    case 'doctor': {
      const r = runPython([path.join(SCRIPTS, 'workflow.py'), 'doctor']);
      return text(r.out);
    }
    case 'recall_memory': {
      const query = String(args.query == null ? '' : args.query);
      if (!query) return text('(no query given)');
      const k = Number.isInteger(args.k) && args.k > 0 ? args.k : 5;
      const r = runPython([path.join(SCRIPTS, 'megamind.py'), 'recall', '-q', query, '-k', String(k)]);
      return text(r.out);
    }
    case 'run_workflow': {
      const spec = safeWorkflowPath(args.workflow);
      const task = String(args.task == null ? '' : args.task);
      if (!task) return text('(no task given)');
      const scriptArgs = [path.join(SCRIPTS, 'workflow.py'), 'run', spec, '--task', task];
      if (args.backend) scriptArgs.push('--backend', safeName(String(args.backend), 'backend'));
      if (Number.isInteger(args.parallel) && args.parallel > 0) scriptArgs.push('--parallel', String(args.parallel));
      if (typeof args.budget === 'number' && args.budget > 0) scriptArgs.push('--budget', String(args.budget));
      if (args.confirm === true) {
        scriptArgs.push('--execute');
      } else {
        // Dry run: still show the plan, and tell the caller how to execute for real.
        const r = runPython(scriptArgs, 600000);
        return text('DRY RUN (confirm=false — no backends spawned, no budget spent).\n'
          + 'To execute for real, call again with confirm=true.\n\n' + r.out);
      }
      const r = runPython(scriptArgs, 1800000);
      return text(r.out);
    }
    case 'run_agent': {
      const agent = safeName(String(args.agent || ''), 'agent name');
      const task = String(args.task == null ? '' : args.task);
      if (!task) return text('(no task given)');
      const backend = args.backend ? safeName(String(args.backend), 'backend') : 'local';
      const scriptArgs = [path.join(SCRIPTS, 'ultron.py'), 'run', '--agent', agent, '--backend', backend, '--quiet'];
      if (args.confirm === true) {
        scriptArgs.push(task);
        const r = runPython(scriptArgs, 600000);
        return text(r.out);
      }
      // Dry run: preview the assembled prompt without calling any model.
      scriptArgs.push('--dry-run', task);
      const r = runPython(scriptArgs);
      return text('DRY RUN (confirm=false — no model called).\n'
        + 'To run against the ' + backend + ' backend, call again with confirm=true.\n\n' + r.out);
    }
    default:
      throw new Error('Unknown tool: ' + name);
  }
}

function launchScript(script, confirm) {
  const full = path.join(SCRIPTS, script);
  const cmd = `powershell -NoProfile -ExecutionPolicy Bypass -File "${full}"`;
  if (confirm !== true) {
    return text('Not launched (confirm=false). To run it: ' + cmd);
  }
  if (!fs.existsSync(full)) return text('Script not found: ' + full);
  const child = spawn('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', full], { detached: true, stdio: 'ignore' });
  child.unref();
  return text('Launched in background: ' + script + ' (pid ' + child.pid + ')');
}

// ---- Dispatch loop --------------------------------------------------------
const rl = readline.createInterface({ input: process.stdin });
rl.on('line', (line) => {
  line = line.trim();
  if (!line) return;
  let msg;
  try { msg = JSON.parse(line); } catch { return; }
  const { id, method, params } = msg;
  try {
    if (method === 'initialize') {
      ok(id, { protocolVersion: '2024-11-05', capabilities: { tools: {} }, serverInfo: { name: 'alfred', version: '1.0.0' } });
    } else if (method === 'tools/list') {
      ok(id, { tools: TOOLS });
    } else if (method === 'tools/call') {
      const res = callTool(params && params.name, params && params.arguments);
      ok(id, res);
    } else if (method === 'ping') {
      ok(id, {});
    } else if (method && method.startsWith('notifications/')) {
      // notifications require no response
    } else if (id !== undefined) {
      err(id, -32601, 'Method not found: ' + method);
    }
  } catch (e) {
    if (id !== undefined) err(id, -32000, e.message);
  }
});
