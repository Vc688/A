import { NextResponse } from "next/server";
import { z } from "zod";
import { prisma } from "@/lib/prisma";
import { audit } from "@/lib/audit";
import { generatePreCallSummary } from "@/lib/intake";

const IntakeSchema = z.object({
  leadId: z.string(), businessType: z.string().optional(), state: z.string(), legalNeedCategory: z.string(), urgency: z.string(), opposingParties: z.string().optional(), existingLawyerInvolvement: z.string().optional(), trademarkOrBrandName: z.string().optional(), contractType: z.string().optional(), companyStatus: z.string().optional(), preferredConsultationTime: z.string().optional()
});

export async function POST(req: Request) {
  const data = IntakeSchema.parse(await req.json());
  const answer = await prisma.intakeAnswer.create({ data });
  const lead = await prisma.lead.update({ where: { id: data.leadId }, data: { conflictStatus: "needs_review" } });
  const summary = generatePreCallSummary({ lead: lead as any, answers: data, conflictStatus: "needs_review" });
  await audit(null, "submit_intake", "Lead", lead.id, { answerId: answer.id });
  return NextResponse.json({ answer, summary });
}
