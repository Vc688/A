import { NextResponse } from "next/server";
import { approveEntity } from "@/lib/actions";
import { currentUserFromHeaders } from "@/lib/currentUser";
import { prisma } from "@/lib/prisma";
import { audit } from "@/lib/audit";
import { evaluateCompliance } from "@/lib/compliance";

export async function POST(req: Request, { params }: { params: { id: string } }) {
  const actor = await currentUserFromHeaders(req.headers);
  const template = await prisma.template.findUniqueOrThrow({ where: { id: params.id } });
  const sampleLead = { id: "sample", email: "sample@example.com", state: template.jurisdiction === "OTHER" ? "NY" : template.jurisdiction, segment: template.segment, doNotContact: false, consentStatus: "opted_in", relationshipStatus: "none" } as any;
  const check = evaluateCompliance({ lead: sampleLead, template: template as any, messageType: template.messageType as any });
  if (check.reasons.some((reason) => reason.includes("Prohibited") || reason.includes("apply law"))) return NextResponse.json({ error: "Template blocked", reasons: check.reasons }, { status: 422 });
  const approval = approveEntity(actor, "approve_template");
  const updated = await prisma.template.update({ where: { id: params.id }, data: approval as any });
  await audit(actor.id, "approve_template", "Template", params.id, { version: template.version });
  return NextResponse.json(updated);
}
