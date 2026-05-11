import { parse } from "csv-parse/sync";
import { stringify } from "csv-stringify/sync";

export const leadCsvHeaders = ["businessName","contactName","email","phone","website","state","source","segment","notes","consentStatus","relationshipStatus","doNotContact"];
export function parseLeadCsv(csv: string) { return parse(csv, { columns: true, skip_empty_lines: true, trim: true }); }
export function toCsv(rows: unknown[]) { return stringify(rows, { header: true }); }
