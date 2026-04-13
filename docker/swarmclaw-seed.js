#!/usr/bin/env node
// swarmclaw-seed.js
// Pre-configure SwarmClaw for Ares: correct Hermes endpoint, Ares Pentest agent, skip setup wizard.
// Copied into the ares-swarmclaw container by setup.sh and run once after first start.

const fs = require('fs');

// Find better-sqlite3 — version-agnostic (works across swarmclaw updates)
function findSqlite() {
  const buildsDir = '/root/.swarmclaw/builds';
  if (fs.existsSync(buildsDir)) {
    const pkg = fs.readdirSync(buildsDir)
      .filter(d => d.startsWith('package-'))
      .sort()
      .pop();
    if (pkg) {
      const p = `${buildsDir}/${pkg}/.next/standalone/node_modules/better-sqlite3`;
      if (fs.existsSync(p)) return p;
    }
  }
  return '/root/.npm-global/lib/node_modules/@swarmclawai/swarmclaw/node_modules/better-sqlite3';
}

const Database = require(findSqlite());
const db = new Database('/root/.swarmclaw/data/swarmclaw.db');
const now = Date.now();

// ── 1. Hermes provider — point to Docker-internal Hermes API ──────────────────
const rawUrl = process.env.HERMES_API_URL || 'http://hermes:8643';
const endpoint = rawUrl.replace(/\/v1$/, '') + '/v1';

const existing = db.prepare("SELECT data FROM provider_configs WHERE id = 'hermes'").get();
if (existing) {
  const d = JSON.parse(existing.data);
  d.endpoint = endpoint;
  d.baseUrl = endpoint;
  d.updatedAt = now;
  db.prepare("UPDATE provider_configs SET data = ? WHERE id = 'hermes'").run(JSON.stringify(d));
  console.log('hermes provider updated →', endpoint);
} else {
  db.prepare("INSERT OR REPLACE INTO provider_configs (id, data) VALUES ('hermes', ?)").run(JSON.stringify({
    id: 'hermes', name: 'Hermes Agent', type: 'builtin',
    endpoint, baseUrl: endpoint,
    models: ['hermes-agent'],
    requiresApiKey: false, credentialId: null, isEnabled: true,
    createdAt: now, updatedAt: now,
  }));
  console.log('hermes provider created →', endpoint);
}

// ── 2. Ares Pentest agent ──────────────────────────────────────────────────────
const AGENT_ID = 'ares-pentest';
if (!db.prepare("SELECT id FROM agents WHERE id = ?").get(AGENT_ID)) {
  db.prepare("INSERT INTO agents (id, data) VALUES (?, ?)").run(AGENT_ID, JSON.stringify({
    id: AGENT_ID,
    name: 'Ares Pentest',
    description: 'Autonomous web & mobile pentester. Claude Opus 4.6 · OWASP WSTG · ZAP · Playwright · sqlmap · nuclei · dalfox · MoBSF · Frida · ADB.',
    provider: 'hermes',
    model: 'hermes-agent',
    systemPrompt: '',
    tools: ['memory', 'manage_tasks', 'web_search'],
    extensions: [], skills: [], skillIds: [], mcpServerIds: [],
    delegationEnabled: false, delegationTargetMode: 'all', delegationTargetAgentIds: [],
    heartbeatEnabled: false, autoRecovery: false, disabled: false,
    proactiveMemory: false,
    autoDraftSkillSuggestions: false,
    role: 'worker',
    origin: 'ares',
    // Disable SwarmClaw sandbox — Hermes manages its own Docker terminal
    sandboxConfig: { enabled: false },
    executeConfig: null,
    createdAt: now, updatedAt: now,
  }));
  console.log('Ares Pentest agent created');
} else {
  console.log('Ares Pentest agent already exists');
}

// ── 3. Mark setup wizard as complete ─────────────────────────────────────────
const sr = db.prepare('SELECT data FROM settings WHERE id = 1').get();
if (sr) {
  const s = JSON.parse(sr.data);
  if (!s.setupCompleted) {
    s.setupCompleted = true;
    db.prepare('UPDATE settings SET data = ? WHERE id = 1').run(JSON.stringify(s));
    console.log('setup wizard marked complete');
  }
} else {
  db.prepare('INSERT INTO settings (id, data) VALUES (1, ?)').run(JSON.stringify({
    setupCompleted: true, userName: 'ares',
    loopMode: 'bounded', agentLoopRecursionLimit: 300,
    supervisorEnabled: true, reflectionEnabled: true,
  }));
  console.log('default settings created');
}

db.close();
console.log('SwarmClaw seed done.');
