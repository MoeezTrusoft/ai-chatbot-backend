import { createHmac, randomUUID } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { resolve } from 'node:path';

loadEnvFile(process.env.ENV_FILE || findDefaultEnvFile());

const PORT = Number(process.env.SESSION_SERVER_PORT || 8787);
const BACKEND_BASE_URL = (
  process.env.BOOKCRAFT_BACKEND_URL ||
  process.env.BOOKCRAFT_BACKEND_BASE_URL ||
  'http://localhost:8000'
).replace(/\/+$/, '');
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function findDefaultEnvFile() {
  const candidates = [
    '.env.production.local',
    '../../.env.production.local',
    '../../../.env.production.local'
  ];
  return candidates.find((candidate) => existsSync(resolve(process.cwd(), candidate))) || '';
}

function loadEnvFile(path) {
  if (!path) return;
  const fullPath = resolve(process.cwd(), path);
  if (!existsSync(fullPath)) return;

  const lines = readFileSync(fullPath, 'utf8').split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const match = trimmed.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (!match) continue;
    const [, key, rawValue] = match;
    if (process.env[key]) continue;
    process.env[key] = rawValue.replace(/^['"]|['"]$/g, '');
  }
}

function isUuid(value) {
  return typeof value === 'string' && UUID_RE.test(value);
}

function b64url(input) {
  return Buffer.from(input).toString('base64url');
}

function signJwt(customerId) {
  const key = process.env.JWT_SIGNING_KEY;
  if (!key) {
    throw new Error('JWT_SIGNING_KEY is missing. Set it in env or provide ENV_FILE=.env.production.local.');
  }

  const exp = Math.floor(Date.now() / 1000) + 60 * 60;
  const header = { alg: 'HS256', typ: 'JWT' };
  const payload = {
    sub: customerId,
    customer_id: customerId,
    exp
  };
  const signingInput = `${b64url(JSON.stringify(header))}.${b64url(JSON.stringify(payload))}`;
  const signature = createHmac('sha256', key).update(signingInput).digest('base64url');
  return { token: `${signingInput}.${signature}`, exp };
}

function fallbackCustomerId() {
  const smokeCustomerId = process.env.SMOKE_CUSTOMER_ID;
  return isUuid(smokeCustomerId) ? smokeCustomerId : randomUUID();
}

function json(res, status, payload) {
  res.writeHead(status, {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET,OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type,Authorization',
    'Content-Type': 'application/json; charset=utf-8'
  });
  res.end(status === 204 ? '' : JSON.stringify(payload));
}

function sessionResponse(res, customerId) {
  const signed = signJwt(customerId);
  json(res, 200, {
    customer_id: customerId,
    chat_token: signed.token,
    expires_at: signed.exp
  });
}

async function proxyTrace(res, threadId, limit) {
  const token = process.env.BOOKCRAFT_ADMIN_ANALYSIS_TOKEN;

  if (!isUuid(threadId)) {
    json(res, 400, { detail: 'threadId must be a valid UUID' });
    return;
  }
  if (!token) {
    json(res, 401, { detail: 'BOOKCRAFT_ADMIN_ANALYSIS_TOKEN is not configured in the session server.' });
    return;
  }

  const target = `${BACKEND_BASE_URL}/api/admin/analysis/traces/${encodeURIComponent(threadId)}?limit=${encodeURIComponent(limit)}`;
  const response = await fetch(target, {
    headers: { Authorization: `Bearer ${token}` }
  });
  const body = await response.text();

  res.writeHead(response.status, {
    'Access-Control-Allow-Origin': '*',
    'Content-Type': response.headers.get('content-type') || 'application/json; charset=utf-8'
  });
  res.end(body);
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`);

  if (req.method === 'OPTIONS') {
    json(res, 204, {});
    return;
  }

  try {
    if (req.method === 'GET' && url.pathname === '/health') {
      json(res, 200, {
        ok: true,
        backend_base_url: BACKEND_BASE_URL,
        has_jwt_signing_key: Boolean(process.env.JWT_SIGNING_KEY),
        has_admin_trace_token: Boolean(process.env.BOOKCRAFT_ADMIN_ANALYSIS_TOKEN)
      });
      return;
    }

    if (req.method === 'GET' && url.pathname === '/api/session') {
      const requestedCustomerId = url.searchParams.get('customer_id');
      if (requestedCustomerId && !isUuid(requestedCustomerId)) {
        json(res, 400, { detail: 'customer_id must be a valid UUID' });
        return;
      }
      sessionResponse(res, requestedCustomerId || fallbackCustomerId());
      return;
    }

    if (req.method === 'GET' && url.pathname === '/session') {
      sessionResponse(res, fallbackCustomerId());
      return;
    }

    const traceMatch = url.pathname.match(/^\/api\/traces\/([^/]+)$/);
    if (req.method === 'GET' && traceMatch) {
      await proxyTrace(res, decodeURIComponent(traceMatch[1]), url.searchParams.get('limit') || '20');
      return;
    }

    json(res, 404, { detail: 'Not found' });
  } catch (error) {
    json(res, 500, { detail: error instanceof Error ? error.message : String(error) });
  }
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`BookCraft dev session server listening on http://localhost:${PORT}`);
  console.log(`Backend target: ${BACKEND_BASE_URL}`);
});
