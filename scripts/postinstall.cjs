#!/usr/bin/env node
// Recreates the farms-sdk programId.js shim after npm ci wipes node_modules.
// @kamino-finance/farms-sdk v3.2.26 removed this file (Codama codegen migration);
// @kamino-finance/klend-sdk v9.1.2 still imports it via a path import.
"use strict";
const fs = require("fs");
const path = require("path");

const shimDir = path.join(
  __dirname, "..", "node_modules",
  "@kamino-finance", "farms-sdk", "dist", "@codegen", "farms"
);
const shimFile = path.join(shimDir, "programId.js");

if (fs.existsSync(shimFile)) {
  // Already present — nothing to do
  process.exit(0);
}

fs.mkdirSync(shimDir, { recursive: true });
fs.writeFileSync(shimFile, `"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.PROGRAM_ID = void 0;
const kit_1 = require("@solana/kit");
exports.PROGRAM_ID = (0, kit_1.address)("FarmsPZpWu9i7Kky8tPN37rs2TpmMrAZrC7S7vJa91Hr");
`);
console.log("[postinstall] farms-sdk programId.js shim created.");
