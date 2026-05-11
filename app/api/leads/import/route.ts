import { NextResponse } from "next/server";
import { parseLeadCsv } from "@/lib/csv";
import { prisma } from "@/lib/prisma";
import { currentUserFromHeaders } from "@/lib/currentUser";
import { audit } from "@/lib/audit";

export async function POST(req: Request) {
  const actor = await currentUserFromHeaders(req.headers);
  const csv = await req.text();
  const rows = parseLeadCsv(csv);
  const imported = [];
  for (const row of rows) {
    imported.push(await prisma.lead.upsert({ where: { email: row.email }, update: { ...row, doNotContact: String(row.doNotContact).toLowerCase() === "true" }, create: { ...row, doNotContact: String(row.doNotContact).toLowerCase() === "true" } }));
  }
  await audit(actor.id, "import_leads_csv", "Lead", "bulk", { count: imported.length, fields: Object.keys(rows[0] ?? {}) });
  return NextResponse.json({ imported: imported.length });
}
