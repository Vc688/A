import type { ConflictStatus, LeadLike } from "./types";

export interface IntakeSummaryInput {
  lead: Pick<LeadLike, "businessName" | "email" | "state" | "segment"> & { businessName?: string | null; contactName?: string; source?: string | null };
  answers: {
    businessType?: string | null;
    state: string;
    legalNeedCategory: string;
    urgency: string;
    opposingParties?: string | null;
    existingLawyerInvolvement?: string | null;
    trademarkOrBrandName?: string | null;
    contractType?: string | null;
    companyStatus?: string | null;
    preferredConsultationTime?: string | null;
  };
  conflictStatus: ConflictStatus;
}

const bannedAdviceLanguage = ["should", "must", "likely liable", "valid claim", "you have a case", "recommend filing", "legal strategy"];

export function generatePreCallSummary(input: IntakeSummaryInput): string {
  const missing = [
    ["opposing parties", input.answers.opposingParties],
    ["existing lawyer involvement", input.answers.existingLawyerInvolvement],
    ["preferred consultation time", input.answers.preferredConsultationTime]
  ].filter(([, value]) => !value).map(([label]) => label);

  const lines = [
    `Lead: ${input.lead.businessName ?? "Unknown business"} (${input.lead.email})`,
    `State / segment: ${input.answers.state || input.lead.state} / ${input.lead.segment}`,
    `Requested help category: ${input.answers.legalNeedCategory}`,
    `Possible service category for attorney review: ${mapServiceCategory(input.answers.legalNeedCategory)}`,
    `Urgency stated by lead: ${input.answers.urgency}`,
    `Business type: ${input.answers.businessType ?? "not provided"}`,
    `Company status: ${input.answers.companyStatus ?? "not provided"}`,
    `Opposing parties: ${input.answers.opposingParties ?? "not provided"}`,
    `Existing lawyer involvement: ${input.answers.existingLawyerInvolvement ?? "not provided"}`,
    `Trademark / brand name: ${input.answers.trademarkOrBrandName ?? "not applicable or not provided"}`,
    `Contract type: ${input.answers.contractType ?? "not applicable or not provided"}`,
    `Preferred consultation time: ${input.answers.preferredConsultationTime ?? "not provided"}`,
    `Missing information: ${missing.length ? missing.join(", ") : "none identified from intake form"}`,
    `Conflict-check status: ${input.conflictStatus}`,
    "Note: This is an intake summary only. It records facts supplied by the lead and does not provide legal advice or create an attorney-client relationship."
  ];
  return lines.join("\n");
}

function mapServiceCategory(category: string): string {
  const lower = category.toLowerCase();
  if (lower.includes("formation") || lower.includes("entity")) return "business formation";
  if (lower.includes("contract")) return "contract review or drafting";
  if (lower.includes("trademark") || lower.includes("brand")) return "trademark/brand protection";
  return "general small-business legal needs";
}

export function summaryContainsAdviceLanguage(summary: string): boolean {
  const lower = summary.toLowerCase();
  return bannedAdviceLanguage.some((phrase) => lower.includes(phrase));
}
