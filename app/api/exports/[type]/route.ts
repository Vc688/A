import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { toCsv } from "@/lib/csv";

export async function GET(_: Request, { params }: { params: { type: string } }) {
  const type = params.type;
  const rows = type === "audit-logs" ? await prisma.auditLog.findMany() :
    type === "sent-messages" ? await prisma.outboundMessage.findMany({ where: { status: "sent" } }) :
    type === "recipients" ? await prisma.lead.findMany() :
    type === "approvals" ? await prisma.auditLog.findMany({ where: { action: { contains: "approve" } } }) :
    type === "campaigns" ? await prisma.campaign.findMany() : [];
  return new NextResponse(toCsv(rows), { headers: { "content-type": "text/csv", "content-disposition": `attachment; filename="${type}.csv"` } });
}
