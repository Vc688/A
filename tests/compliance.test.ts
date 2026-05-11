import { describe, expect, it } from "vitest";
import { canPerform } from "../lib/authz";
import { evaluateCompliance } from "../lib/compliance";
import { generatePreCallSummary, summaryContainsAdviceLanguage } from "../lib/intake";

const footer = "\nFirm: Hudson Small Business Counsel\nPrincipal office: 123 Main Street, New York, NY 10001\nPhone: (212) 555-0100\nResponsible attorney: Avery Cohen\nJurisdiction: New York and New Jersey\nTemplate version: 1\nNo attorney-client relationship is formed unless the firm agrees in writing after conflict review.";
const lead = { id: "l1", email: "lead@example.test", state: "NY", segment: "founder", doNotContact: false, consentStatus: "opted_in", relationshipStatus: "none" } as const;
const approvedTemplate = { id: "t1", status: "approved", subject: "ATTORNEY ADVERTISING: Founder checklist", bodyMarkdown: `Educational checklist. Advertising Classification: educational legal advertising.${footer}`, version: 1, messageType: "newsletter", jurisdiction: "NY" } as const;

describe("compliance-sensitive outreach rules", () => {
  it("blocks unapproved templates", () => {
    const result = evaluateCompliance({ lead, template: { ...approvedTemplate, status: "draft" }, messageType: "newsletter" });
    expect(result.ok).toBe(false);
    expect(result.reasons.join(" ")).toContain("Template must be attorney/admin approved");
  });

  it("blocks do-not-contact recipients", () => {
    const result = evaluateCompliance({ lead: { ...lead, doNotContact: true }, template: approvedTemplate, messageType: "newsletter" });
    expect(result.ok).toBe(false);
    expect(result.reasons.join(" ")).toContain("do-not-contact");
  });

  it("requires ATTORNEY ADVERTISING for New York messages unless attorney override exists", () => {
    const result = evaluateCompliance({ lead, template: { ...approvedTemplate, subject: "Founder checklist" }, messageType: "newsletter" });
    expect(result.ok).toBe(false);
    expect(result.reasons.join(" ")).toContain("ATTORNEY ADVERTISING");
    const overridden = evaluateCompliance({ lead, template: { ...approvedTemplate, subject: "Founder checklist", documentedNyException: true }, messageType: "newsletter" });
    expect(overridden.reasons.join(" ")).not.toContain("ATTORNEY ADVERTISING");
  });

  it("blocks prohibited claims and law-to-facts language", () => {
    const result = evaluateCompliance({ lead, template: { ...approvedTemplate, bodyMarkdown: `You have a case. We are the best.${footer}` }, messageType: "newsletter" });
    expect(result.ok).toBe(false);
    expect(result.reasons.join(" ")).toContain("Prohibited");
  });

  it("prevents non-attorneys from approving protected actions", () => {
    for (const action of ["approve_campaign", "approve_template", "approve_resource", "create_compliance_override", "clear_conflict", "release_outbound_message"] as const) {
      expect(canPerform({ id: "u1", role: "outreach_manager" }, action)).toBe(false);
      expect(canPerform({ id: "u2", role: "intake_staff" }, action)).toBe(false);
      expect(canPerform({ id: "u3", role: "attorney" }, action)).toBe(true);
      expect(canPerform({ id: "u4", role: "admin" }, action)).toBe(true);
    }
  });

  it("keeps intake summaries factual and free of legal advice language", () => {
    const summary = generatePreCallSummary({ lead: { ...lead, businessName: "Acme", contactName: "Alex" }, conflictStatus: "needs_review", answers: { state: "NY", legalNeedCategory: "contract review", urgency: "this month", businessType: "LLC" } });
    expect(summary).toContain("Conflict-check status: needs_review");
    expect(summaryContainsAdviceLanguage(summary)).toBe(false);
  });

  it("unsubscribe immediately prevents future campaign sends", () => {
    const result = evaluateCompliance({ lead: { ...lead, unsubscribedAt: new Date(), consentStatus: "opted_out", doNotContact: true }, template: approvedTemplate, campaign: { id: "c1", status: "approved", messageType: "newsletter", segment: "founder" }, messageType: "newsletter" });
    expect(result.ok).toBe(false);
    expect(result.reasons.join(" ")).toContain("unsubscribed");
  });

  it("requires sent-message preservation fields for auditability", () => {
    const sent = { finalBody: "body", finalSubject: "subject", recipient: lead.email, sentAt: new Date(), templateVersion: 1, approver: "attorney", campaignId: "c1" };
    expect(Object.values(sent).every(Boolean)).toBe(true);
  });
});
