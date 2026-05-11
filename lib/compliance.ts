import { canPerform } from "./authz";
import { findProhibitedClaims, defaultProhibitedClaims } from "./prohibitedClaims";
import type { CampaignLike, LeadLike, OverrideLike, TemplateLike, UserLike } from "./types";

export interface ComplianceInput {
  lead: LeadLike;
  template: TemplateLike;
  campaign?: CampaignLike | null;
  messageType: TemplateLike["messageType"];
  finalSubject?: string;
  finalBody?: string;
  prohibitedDictionary?: string[];
  overrides?: OverrideLike[];
}

export interface ComplianceResult { ok: boolean; reasons: string[]; requiredLabels: string[] }

export function isNyTarget(lead: LeadLike, template: TemplateLike): boolean {
  return lead.state.toUpperCase() === "NY" || template.jurisdiction === "NY";
}

export function isNjTarget(lead: LeadLike, template: TemplateLike): boolean {
  return lead.state.toUpperCase() === "NJ" || template.jurisdiction === "NJ";
}

function hasOverride(overrides: OverrideLike[] | undefined, targetType: string, targetId: string): boolean {
  return Boolean(overrides?.some((override) => override.targetType === targetType && override.targetId === targetId));
}

export function evaluateCompliance(input: ComplianceInput): ComplianceResult {
  const subject = input.finalSubject ?? input.template.subject;
  const body = input.finalBody ?? input.template.bodyMarkdown;
  const reasons: string[] = [];
  const requiredLabels: string[] = [];
  const text = `${subject}\n${body}`;

  if (input.template.status !== "approved") reasons.push("Template must be attorney/admin approved.");
  if (input.campaign && input.campaign.status !== "approved") reasons.push("Campaign must be attorney/admin approved.");
  if (input.lead.doNotContact || input.lead.unsubscribedAt || input.lead.consentStatus === "opted_out") reasons.push("Recipient is unsubscribed, opted out, or marked do-not-contact.");
  if (!input.lead.state || !["NY", "NJ", "OTHER", "PA", "CT"].includes(input.lead.state.toUpperCase())) reasons.push("Recipient state must be validated before outreach.");
  if (input.messageType === "cold_outreach") reasons.push("Cold targeted solicitation email is review-only in v1 and cannot be auto-sent.");
  if (["newsletter", "resource_follow_up"].includes(input.messageType) && !["opted_in", "existing_relationship"].includes(input.lead.consentStatus)) reasons.push("Automated newsletter/resource follow-up requires opted-in or existing relationship status.");

  const prohibited = findProhibitedClaims(text, input.prohibitedDictionary ?? defaultProhibitedClaims);
  if (prohibited.length) reasons.push(`Prohibited or unsupported legal-marketing claims detected: ${prohibited.join(", ")}.`);
  if (/\b(you|your)\b.{0,80}\b(should|must|liable|violated|infringes|breach|valid claim|case)\b/i.test(text)) reasons.push("Message appears to apply law to recipient-specific facts.");

  if (isNyTarget(input.lead, input.template) && input.messageType !== "transactional" && input.messageType !== "legal_administrative") {
    requiredLabels.push("ATTORNEY ADVERTISING");
    if (!subject.startsWith("ATTORNEY ADVERTISING") && !input.template.documentedNyException && !hasOverride(input.overrides, "ny_advertising_label", input.template.id)) {
      reasons.push('New York-targeted legal advertising requires subject prefix "ATTORNEY ADVERTISING" unless a documented attorney exception exists.');
    }
  }

  if (isNjTarget(input.lead, input.template)) {
    requiredLabels.push("advertising classification");
    if (!/Advertising Classification:/i.test(body)) reasons.push("New Jersey messages require an advertising classification field.");
    if (/\b(better|most successful|leading|premier|#1|no\. ?1)\b/i.test(text)) reasons.push("New Jersey messages may not contain misleading comparative claims.");
    if (input.template.containsTestimonial && !hasOverride(input.overrides, "testimonial_approval", input.template.id)) reasons.push("Testimonial or endorsement language requires attorney approval.");
  }

  const footerChecks = ["Firm:", "Principal office:", "Phone:", "Responsible attorney:", "Jurisdiction:", "Template version:", "No attorney-client relationship"];
  for (const footer of footerChecks) if (!body.includes(footer)) reasons.push(`Required footer/disclaimer missing: ${footer}`);

  return { ok: reasons.length === 0, reasons, requiredLabels };
}

export function assertAttorneyOverride(actor: UserLike, reason: string): void {
  if (!canPerform(actor, "create_compliance_override")) throw new Error("Only attorney/admin can create compliance overrides.");
  if (!reason || reason.trim().length < 12) throw new Error("Compliance override requires a documented reason.");
}
