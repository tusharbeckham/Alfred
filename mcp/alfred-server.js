#!/usr/bin/env node
'use strict';
/*
 * Alfred MCP Server — a dependency-free Model Context Protocol server (JSON-RPC 2.0 over
 * stdio, newline-delimited). Exposes Alfred's own tools: memory access, eval results,
 * agent listing, and (confirmation-gated) automation triggers.
 *
 * Run: node C:\Alfred\mcp\alfred-server.js
 * Configured in .kiro/settings/mcp.json as the "alfred" server.
 */
const fs = require('fs');
const path = require('path');
const readline = require('readline');
const { spawn } = require('child_process');

const ROOT = 'C:\\Alfred';
const MEMORY = path.join(ROOT, 'memory');
const RESULTS = path.join(ROOT, 'evals', 'results');
const AGENTS = path.join(ROOT, '.kiro', 'agents');
const SCRIPTS = path.join(ROOT, 'scripts');

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
