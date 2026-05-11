import { NextResponse } from "next/server";
import { approveEntity } from "@/lib/actions";
import { currentUserFromHeaders } from "@/lib/currentUser";
import { prisma } from "@/lib/prisma";
import { audit } from "@/lib/audit";

export async function POST(req: Request, { params }: { params: { id: string } }) {
  const actor = await currentUserFromHeaders(req.headers);
  const approval = approveEntity(actor, "approve_campaign");
  const updated = await prisma.campaign.update({ where: { id: params.id }, data: approval as any });
  await audit(actor.id, "approve_campaign", "Campaign", params.id);
  return NextResponse.json(updated);
}
