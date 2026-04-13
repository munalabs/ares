#!/usr/bin/env node
// swarmclaw-seed.js
// Pre-configure SwarmClaw for Ares: correct Hermes endpoint, Ares Pentest agent, skip setup wizard.
// Copied into the ares-swarmclaw container by setup.sh and run once after first start.

const fs = require('fs');
const crypto = require('crypto');

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

// Read CREDENTIAL_SECRET from SwarmClaw's generated env file.
// SwarmClaw auto-generates this on first start and persists it to .env.local
// (inside the Next.js standalone build dir) so credentials survive restarts.
function loadCredentialSecret() {
  // Try .env.local inside the built standalone dir
  const buildsDir = '/root/.swarmclaw/builds';
  if (fs.existsSync(buildsDir)) {
    const pkg = fs.readdirSync(buildsDir)
      .filter(d => d.startsWith('package-'))
      .sort()
      .pop();
    if (pkg) {
      const envLocal = `${buildsDir}/${pkg}/.next/standalone/.env.local`;
      if (fs.existsSync(envLocal)) {
        const content = fs.readFileSync(envLocal, 'utf8');
        const match = content.match(/^CREDENTIAL_SECRET=([a-fA-F0-9]+)/m);
        if (match) return match[1];
      }
    }
  }
  // Fallback: DATA_DIR/.env.generated
  const generated = '/root/.swarmclaw/data/.env.generated';
  if (fs.existsSync(generated)) {
    const content = fs.readFileSync(generated, 'utf8');
    const match = content.match(/^CREDENTIAL_SECRET=([a-fA-F0-9]+)/m);
    if (match) return match[1];
  }
  return null;
}

// AES-256-GCM encryption — matches SwarmClaw's encryptKey() in storage.ts
function encryptKey(plaintext, credSecretHex) {
  const key = Buffer.from(credSecretHex, 'hex');
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
  let encrypted = cipher.update(plaintext, 'utf8', 'hex');
  encrypted += cipher.final('hex');
  const tag = cipher.getAuthTag().toString('hex');
  return iv.toString('hex') + ':' + tag + ':' + encrypted;
}

const Database = require(findSqlite());
const db = new Database('/root/.swarmclaw/data/swarmclaw.db');
const now = Date.now();

// ── 1. Hermes provider — point to Docker-internal Hermes API ──────────────────
const rawUrl = process.env.HERMES_API_URL || 'http://hermes:8643';
const endpoint = rawUrl.replace(/\/v1$/, '') + '/v1';

// Store Hermes API key as a credential (needed since hermes binds 0.0.0.0 with auth)
let credentialId = null;
const hermesApiKey = process.env.HERMES_API_KEY || '';
if (hermesApiKey) {
  const credSecret = loadCredentialSecret();
  if (credSecret) {
    const CRED_ID = 'cred_ares_hermes';
    const encryptedKey = encryptKey(hermesApiKey, credSecret);
    const credData = JSON.stringify({
      id: CRED_ID,
      provider: 'hermes',
      name: 'Hermes Ares',
      encryptedKey,
      createdAt: now,
    });
    db.prepare("INSERT OR REPLACE INTO credentials (id, data) VALUES (?, ?)").run(CRED_ID, credData);
    credentialId = CRED_ID;
    console.log('hermes credential stored (encrypted)');
  } else {
    console.warn('CREDENTIAL_SECRET not found — skipping hermes credential storage');
  }
}

const existing = db.prepare("SELECT data FROM provider_configs WHERE id = 'hermes'").get();
if (existing) {
  const d = JSON.parse(existing.data);
  d.endpoint = endpoint;
  d.baseUrl = endpoint;
  if (credentialId) d.credentialId = credentialId;
  d.updatedAt = now;
  db.prepare("UPDATE provider_configs SET data = ? WHERE id = 'hermes'").run(JSON.stringify(d));
  console.log('hermes provider updated →', endpoint);
} else {
  db.prepare("INSERT OR REPLACE INTO provider_configs (id, data) VALUES ('hermes', ?)").run(JSON.stringify({
    id: 'hermes', name: 'Hermes Agent', type: 'builtin',
    endpoint, baseUrl: endpoint,
    models: ['hermes-agent'],
    requiresApiKey: false, credentialId, isEnabled: true,
    createdAt: now, updatedAt: now,
  }));
  console.log('hermes provider created →', endpoint);
}

// ── 2. Ares Pentest agent ──────────────────────────────────────────────────────
const AGENT_ID = 'ares-pentest';
const agentRow = db.prepare("SELECT data FROM agents WHERE id = ?").get(AGENT_ID);
if (!agentRow) {
  db.prepare("INSERT INTO agents (id, data) VALUES (?, ?)").run(AGENT_ID, JSON.stringify({
    id: AGENT_ID,
    name: 'Ares Pentest',
    description: 'Autonomous web & mobile pentester. Claude Opus 4.6 · OWASP WSTG · ZAP · Playwright · sqlmap · nuclei · dalfox · MoBSF · Frida · ADB.',
    provider: 'hermes',
    model: 'hermes-agent',
    // apiEndpoint + credentialId must be on the agent record — provider_configs fields are not used for routing
    apiEndpoint: endpoint,
    credentialId: credentialId,
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
  // Ensure apiEndpoint is set on existing agent (idempotent fix)
  const d = JSON.parse(agentRow.data);
  if (d.apiEndpoint !== endpoint || d.credentialId !== credentialId) {
    d.apiEndpoint = endpoint;
    if (credentialId) d.credentialId = credentialId;
    d.updatedAt = now;
    db.prepare('UPDATE agents SET data=? WHERE id=?').run(JSON.stringify(d), AGENT_ID);
    console.log('Ares Pentest agent updated → apiEndpoint:', endpoint, '| credentialId:', credentialId);
  } else {
    console.log('Ares Pentest agent already exists');
  }
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
