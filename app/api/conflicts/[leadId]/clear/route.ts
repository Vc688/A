import { NextResponse } from "next/server";
import { approveEntity } from "@/lib/actions";
import { currentUserFromHeaders } from "@/lib/currentUser";
import { prisma } from "@/lib/prisma";
import { audit } from "@/lib/audit";

export async function POST(req: Request, { params }: { params: { leadId: string } }) {
  const actor = await currentUserFromHeaders(req.headers);
  approveEntity(actor, "clear_conflict");
  const lead = await prisma.lead.update({ where: { id: params.leadId }, data: { conflictStatus: "cleared", conflictClearedAt: new Date(), conflictClearedById: actor.id === "demo" ? undefined : actor.id } });
  await audit(actor.id, "clear_conflict", "Lead", params.leadId);
  return NextResponse.json(lead);
}
