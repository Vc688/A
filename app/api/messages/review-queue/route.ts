import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

export async function GET() {
  const messages = await prisma.outboundMessage.findMany({ where: { status: { in: ["pending_review", "blocked"] } }, include: { lead: true, template: true, campaign: true }, orderBy: { createdAt: "desc" }, take: 100 });
  return NextResponse.json(messages);
}
