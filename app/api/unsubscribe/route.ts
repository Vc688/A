import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { audit } from "@/lib/audit";

export async function POST(req: Request) {
  const { email } = await req.json();
  const lead = await prisma.lead.update({ where: { email }, data: { doNotContact: true, consentStatus: "opted_out", unsubscribedAt: new Date() } });
  await audit(null, "unsubscribe", "Lead", lead.id, { email });
  return NextResponse.json({ ok: true });
}
