import { NextResponse } from "next/server";
import { currentUserFromHeaders } from "@/lib/currentUser";
import { prisma } from "@/lib/prisma";
import { audit } from "@/lib/audit";
import { releaseMessage } from "@/lib/actions";

export async function POST(req: Request, { params }: { params: { id: string } }) {
  const actor = await currentUserFromHeaders(req.headers);
  const message = await prisma.outboundMessage.findUniqueOrThrow({ where: { id: params.id }, include: { lead: true, template: true, campaign: true } });
  const sent = await releaseMessage(actor, message);
  const updated = await prisma.outboundMessage.update({ where: { id: params.id }, data: { status: "sent", approvedAt: new Date(), approvedById: actor.id === "demo" ? undefined : actor.id, sentAt: new Date(), finalSubject: message.finalSubject ?? message.subjectPreview, finalBody: message.finalBody ?? message.bodyPreview, providerMessageId: sent.providerMessageId } });
  await audit(actor.id, "release_outbound_message", "OutboundMessage", params.id, { providerMessageId: sent.providerMessageId, campaignId: message.campaignId, templateVersion: message.templateVersion });
  return NextResponse.json(updated);
}
