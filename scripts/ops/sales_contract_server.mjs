// Isolated contract probe using a trusted Sales checkout. No Prisma or business writes.
// node scripts/ops/sales_contract_server.mjs <sales-checkout> <temporary-output-directory>
import {generateKeyPairSync, randomUUID} from "node:crypto";
import {createRequire, registerHooks} from "node:module";
import {pathToFileURL} from "node:url";
import {resolve, join} from "node:path";
import {readFile, writeFile} from "node:fs/promises";
import {runInNewContext} from "node:vm";
import {once} from "node:events";

const sales = resolve(process.argv[2]);
const output = resolve(process.argv[3]);
const requireSales = createRequire(join(sales, "package.json"));
const express = requireSales("express");
const {z} = requireSales("zod");
const {SignJWT} = await import(pathToFileURL(requireSales.resolve("jose")));
const source = file => import(pathToFileURL(join(sales, file)));
const {requireInternalService} = await source("src/middleware/internal-service.middleware.js");
const {CONSULTATION_SPECS} = await source("src/mcp/consultation-tools.js");
const {privateKey, publicKey} = generateKeyPairSync("ed25519");
const tenantId = randomUUID();
process.env.SERVICE_JWT_PUBLIC_KEYS_JSON = JSON.stringify({probe: publicKey.export({format: "jwk"})});
process.env.SERVICE_JWT_ACCEPT_HS256 = "false";
process.env.SERVICE_JWT_AUDIENCE = "internal";
process.env.MCP_INTERNAL_SECRET = "synthetic-contract-probe";
const scopes = ["sales.customer.read", "sales.customer.update", "sales.lead.create",
    "sales.lead.read", "sales.lead.update", "sales.config.read"];
const token = await new SignJWT({tenantId, userId: "chatbot-probe", scope: scopes.join(" ")})
    .setProtectedHeader({alg: "EdDSA", kid: "probe"}).setSubject("chatbot-probe")
    .setIssuer("erp-rbac").setAudience("internal").setIssuedAt().setExpirationTime("5m").sign(privateKey);
await writeFile(join(output, "service.jwt"), token, {mode: 0o600});
const schemas = Object.fromEntries(CONSULTATION_SPECS.map(spec => [spec.name, spec.schema]));
// Read these exact pure schema expressions without importing Prisma-dependent handlers.
const registry = (await readFile(join(sales, "src/mcp/toolRegistry.js"), "utf8")).replaceAll("\r", "");
for (const name of ["sales_customer_get", "sales_customer_update", "sales_lead_create"]) {
    const block = registry.slice(registry.indexOf(name + ": {"));
    const expression = block.slice(block.indexOf("schema: ") + 8, block.indexOf(",\n    handler:"))
        .replaceAll("\r", "");
    schemas[name] = runInNewContext("(" + expression + ")", {z, uuid: z.string().uuid()});
}
// Load Sales' actual router/lifecycle/discovery/authz. Only database handlers and
// the Prisma-dependent error sanitizer are replaced in this isolated probe.
const handlersKey = Symbol.for("bookcraft.sales.contract.handlers");
globalThis[handlersKey] = Object.fromEntries(Object.entries(schemas).map(([name, schema]) => [
    name, {schema, handler: async (arguments_, ctx) => ({
        accepted: true, tenantId: ctx.tenantId, tool: name, arguments: arguments_
    })}
]));
const registryUrl = pathToFileURL(join(sales, "src/mcp/toolRegistry.js")).href;
const sanitizerUrl = pathToFileURL(join(sales, "src/lib/prisma-error.js")).href;
const hooks = registerHooks({load(url, context, nextLoad) {
    if (url === registryUrl) return {format: "module", shortCircuit: true, source: `
        export const TOOL_HANDLERS = globalThis[Symbol.for("bookcraft.sales.contract.handlers")];
        export const TOOL_NAMES = new Set(Object.keys(TOOL_HANDLERS));
    `};
    if (url === sanitizerUrl) return {format: "module", shortCircuit: true, source: `
        export const isPrismaError = () => false;
        export const sanitizeError = error => error;
    `};
    return nextLoad(url, context);
}});
const {createMcpRouter} = await source("src/mcp/mcpRouter.js");
hooks.deregister();
delete globalThis[handlersKey];
const app = express();
app.use(express.json());
app.use("/mcp", requireInternalService, createMcpRouter());
const server = app.listen(0, "127.0.0.1");
await once(server, "listening");
await writeFile(join(output, "connection.json"), JSON.stringify({
    sales_mcp_url: "http://127.0.0.1:" + server.address().port + "/mcp",
    sales_tenant_id: tenantId, sales_actor_id: "chatbot-probe",
    sales_mcp_internal_secret: process.env.MCP_INTERNAL_SECRET,
    sales_service_jwt_file: join(output, "service.jwt")
}));
console.log("Sales contract probe ready (authentication, authorization, schemas; no database)");
process.on("SIGTERM", () => server.close(() => process.exit(0)));
