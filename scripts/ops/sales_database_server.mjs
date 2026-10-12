// Disposable cross-repository fixture. Uses the real Sales router, services and PostgreSQL.
// SALES_TEST_ADMIN_URL must name a loopback PostgreSQL administrator database.
import { generateKeyPairSync, randomBytes, randomUUID } from 'node:crypto';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { resolve, join } from 'node:path';
import { readFile, readdir, writeFile } from 'node:fs/promises';
import { once } from 'node:events';
import { createInterface } from 'node:readline';

const sales = resolve(process.argv[2]);
const output = resolve(process.argv[3]);
const adminUrl = new URL(process.env.SALES_TEST_ADMIN_URL);
if (!['localhost', '127.0.0.1', '[::1]'].includes(adminUrl.hostname))
  throw Error('The disposable integration fixture requires a loopback database');
const requireSales = createRequire(join(sales, 'package.json'));
const { Client: PgClient } = requireSales('pg');
const express = requireSales('express');
const { SignJWT } = await import(pathToFileURL(requireSales.resolve('jose')));
const source = file => import(pathToFileURL(join(sales, file)));
const suffix = randomBytes(8).toString('hex');
const database = `chatbot_sales_test_${suffix}`;
const role = `chatbot_test_${suffix}`;
const password = randomBytes(24).toString('hex');
const tenantId = randomUUID();
const otherTenantId = randomUUID();
const customerIds = [randomUUID(), randomUUID(), randomUUID()];
const ownerId = 'synthetic-csr';
const admin = new PgClient({ connectionString: adminUrl.toString() });
let db, prisma, server, databaseCreated = false, roleCreated = false, closing = false;

async function close() {
  if (closing) return;
  closing = true;
  if (server) {
    server.closeAllConnections();
    await new Promise(done => server.close(done));
  }
  if (prisma) await prisma.$disconnect();
  if (db) await db.end();
  if (databaseCreated) await admin.query(`DROP DATABASE "${database}" WITH (FORCE)`);
  if (roleCreated) await admin.query(`DROP ROLE "${role}"`);
  await admin.end();
}

try {
  await admin.connect();
  await admin.query(`CREATE DATABASE "${database}"`);
  databaseCreated = true;
  const databaseUrl = new URL(adminUrl);
  databaseUrl.pathname = '/' + database;
  db = new PgClient({ connectionString: databaseUrl.toString() });
  await db.connect();
  for (const name of (await readdir(join(sales, 'prisma/migrations'))).sort()) {
    if (name === 'migration_lock.toml') continue;
    await db.query(await readFile(join(sales, 'prisma/migrations', name, 'migration.sql'), 'utf8'));
  }
  await admin.query(`CREATE ROLE "${role}" LOGIN PASSWORD '${password}' NOSUPERUSER NOBYPASSRLS`);
  roleCreated = true;
  await db.query(`GRANT USAGE ON SCHEMA public TO "${role}"`);
  await db.query(`GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "${role}"`);
  await db.query(`GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "${role}"`);
  const { DEFAULT_POLICY } = await source('src/services/consultation-policy.js');
  for (const [index, id] of customerIds.entries()) {
    await db.query('INSERT INTO "Customer" (id,"tenantId",name,phone,"updatedAt") VALUES ($1,$2,$3,$4,now())',
      [id, index === 2 ? otherTenantId : tenantId, 'Synthetic Customer ' + index, '+12025550123']);
  }
  const agent = randomUUID();
  await db.query('INSERT INTO "SalesAgentProfile" (id,"tenantId","userId","participation","updatedAt") VALUES ($1,$2,$3,true,now())', [agent, tenantId, ownerId]);
  await db.query('INSERT INTO "SalesAgentSkill" (id,"tenantId","agentProfileId",niche,"updatedAt") VALUES ($1,$2,$3,\'test\',now())', [randomUUID(), tenantId, agent]);
  await db.query('INSERT INTO "ConsultationConfig" (id,"tenantId",policy,"updatedAt") VALUES ($1,$2,$3,now())',
    [randomUUID(), tenantId, { ...DEFAULT_POLICY, enabled: true, ownerIds: [ownerId] }]);
  const runtimeUrl = new URL(databaseUrl);
  runtimeUrl.username = role;
  runtimeUrl.password = password;
  runtimeUrl.searchParams.set('options', `-c app.tenant_id=${tenantId}`);
  process.env.DATABASE_URL = runtimeUrl.toString();
  process.env.NODE_ENV = 'test';
  delete process.env.SALES_RECORD_SCOPES_JSON;
  const { privateKey, publicKey } = generateKeyPairSync('ed25519');
  process.env.SERVICE_JWT_PUBLIC_KEYS_JSON = JSON.stringify({ fixture: publicKey.export({ format: 'jwk' }) });
  process.env.SERVICE_JWT_ACCEPT_HS256 = 'false';
  process.env.SERVICE_JWT_AUDIENCE = 'internal';
  process.env.MCP_INTERNAL_SECRET = randomBytes(24).toString('hex');
  process.env.SALES_FIXTURE_BOT_KEY = randomBytes(32).toString('hex');
  const scopes = ['sales.customer.read', 'sales.customer.update', 'sales.lead.create',
    'sales.lead.read', 'sales.lead.update', 'sales.config.read'];
  const actor = 'synthetic-chatbot';
  const token = await new SignJWT({ tenantId, userId: actor, scope: scopes.join(' ') })
    .setProtectedHeader({ alg: 'EdDSA', kid: 'fixture' }).setSubject(actor)
    .setIssuer('erp-rbac').setAudience('internal').setIssuedAt().setExpirationTime('15m').sign(privateKey);
  await writeFile(join(output, 'service.jwt'), token, { mode: 0o600 });
  ({ prisma } = await source('src/lib/prisma.js'));
  const identity = await prisma.$queryRawUnsafe('SELECT current_user AS role, current_setting(\'app.tenant_id\') AS tenant');
  if (identity[0].role !== role || identity[0].tenant !== tenantId) throw Error('Runtime identity mismatch');
  const { requireInternalService } = await source('src/middleware/internal-service.middleware.js');
  const { createMcpRouter } = await source('src/mcp/mcpRouter.js');
  const { botToken } = await source('src/website/policy.js');
  const app = express();
  app.use(express.json());
  app.use('/mcp', requireInternalService, createMcpRouter());
  server = app.listen(0, '127.0.0.1');
  await once(server, 'listening');
  await writeFile(join(output, 'connection.json'), JSON.stringify({
    settings: {
      sales_mcp_url: `http://127.0.0.1:${server.address().port}/mcp`,
      sales_tenant_id: tenantId, sales_actor_id: actor,
      sales_mcp_internal_secret: process.env.MCP_INTERNAL_SECRET,
      sales_service_jwt_file: join(output, 'service.jwt'),
      jwt_signing_key: process.env.SALES_FIXTURE_BOT_KEY,
    },
    customer_ids: customerIds, owner_id: ownerId,
    customer_tokens: customerIds.map(id => botToken({ botSigningKeyEnv: 'SALES_FIXTURE_BOT_KEY' }, id)),
    database_url: databaseUrl.toString(), runtime_database_url: runtimeUrl.toString(),
  }), { mode: 0o600 });
  console.log('Sales database fixture ready (real handlers; non-superuser with tenant RLS)');
  const input = createInterface({ input: process.stdin });
  input.on('line', async line => {
    if (line === 'stop') { input.close(); process.stdin.destroy(); await close(); }
  });
  process.on('SIGTERM', async () => { input.close(); process.stdin.destroy(); await close(); });
} catch (error) {
  console.error('Sales database fixture failed:', error);
  await close();
  process.exitCode = 1;
}
