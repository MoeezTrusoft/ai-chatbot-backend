// Isolated contract probe using a trusted Sales checkout. No Prisma or business writes.
// node scripts/ops/sales_contract_server.mjs <sales-checkout> <temporary-output-directory>
import {generateKeyPairSync, randomUUID} from "node:crypto";
import {createRequire} from "node:module";
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
const {requireMcpInternal} = await source("src/mcp/mcp.auth.js");
const {authorizeTool} = await source("src/mcp/utils/assertSalesPermission.js");
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
const app = express();
app.use(express.json());
app.post("/mcp", requireInternalService, (req, res) => {
    const gate = requireMcpInternal(req);
    if (!gate.ok) return res.status(gate.status).json({error: "coarse_gate"});
    const {id, params, method, jsonrpc} = req.body;
    if (method !== "tools/call" || jsonrpc !== "2.0") return res.status(400).end();
    const denied = authorizeTool(params.name, req);
    const parsed = schemas[params.name]?.safeParse(params.arguments);
    if (denied || !parsed?.success) {
        return res.json({jsonrpc: "2.0", id, error: {
            code: -32000, message: "contract rejected", data: {status: denied ? 403 : 422}
        }});
    }
    return res.json({jsonrpc: "2.0", id, result: {content: [{type: "text", text: JSON.stringify({
        accepted: true, tenantId: req.internalService.tenantId, tool: params.name,
        arguments: parsed.data
    })}]}});
});
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
