import { PrismaClient } from "@prisma/client";
import { defaultProhibitedClaims } from "../lib/prohibitedClaims";

const prisma = new PrismaClient();
const disclaimer = "This educational material is attorney advertising where applicable. It is general information only, is not legal advice, and does not create an attorney-client relationship unless the firm agrees in writing after conflict review.";
const footer = (version: number) => `\n\n---\nFirm: ${process.env.FIRM_NAME ?? "Hudson Small Business Counsel"}\nPrincipal office: ${process.env.FIRM_PRINCIPAL_OFFICE_ADDRESS ?? "123 Main Street, New York, NY 10001"}\nPhone: ${process.env.FIRM_PHONE ?? "(212) 555-0100"}\nResponsible attorney: ${process.env.RESPONSIBLE_ATTORNEY ?? "Avery Cohen"}\nJurisdiction: ${process.env.FIRM_JURISDICTION ?? "New York and New Jersey"}\nTemplate version: ${version}\nNo attorney-client relationship is formed unless the firm agrees in writing after conflict review. You may unsubscribe at any time.`;

async function main() {
  const admin = await prisma.user.upsert({ where: { email: "admin@example.test" }, update: {}, create: { id: "seed_admin", email: "admin@example.test", name: "Admin Reviewer", role: "admin" } });
  await prisma.user.upsert({ where: { email: "attorney@example.test" }, update: {}, create: { id: "seed_attorney", email: "attorney@example.test", name: "Avery Cohen", role: "attorney" } });
  await prisma.user.upsert({ where: { email: "outreach@example.test" }, update: {}, create: { id: "seed_outreach", email: "outreach@example.test", name: "Outreach Manager", role: "outreach_manager" } });

  for (const phrase of defaultProhibitedClaims) await prisma.prohibitedClaim.upsert({ where: { phrase }, update: { enabled: true }, create: { phrase } });

  const leads = [
    { businessName: "North Star Studio LLC", contactName: "Maya Founder", email: "maya@example.test", phone: "212-555-0101", website: "https://example.test", state: "NY", source: "event", segment: "founder", notes: "Asked for checklist at workshop.", consentStatus: "opted_in", relationshipStatus: "none", doNotContact: false },
    { businessName: "Garden State Services", contactName: "Noah Owner", email: "noah@example.test", phone: "201-555-0102", website: "", state: "NJ", source: "referral", segment: "existing_business_contracts", notes: "Manual note: service contract renewal in Q3.", consentStatus: "existing_relationship", relationshipStatus: "professional_contact", doNotContact: false },
    { businessName: "BrightMark Goods", contactName: "Iris Brand", email: "iris@example.test", phone: "", website: "", state: "NY", source: "newsletter", segment: "trademark_brand", notes: "Interested in brand readiness checklist.", consentStatus: "unknown", relationshipStatus: "none", doNotContact: false }
  ];
  for (const lead of leads) await prisma.lead.upsert({ where: { email: lead.email }, update: lead as any, create: lead as any });

  const sequenceData = [
    ["founder formation checklist", "founder", "Founder Legal Checklist"],
    ["contract risk review", "existing_business_contracts", "Contract Red Flags for Service Businesses"],
    ["trademark brand review", "trademark_brand", "Brand and Trademark Readiness Checklist"]
  ] as const;
  for (const [sequenceName, segment, resource] of sequenceData) {
    for (const [idx, step] of ["resource offer", "follow-up", "consultation invitation"].entries()) {
      const version = 1;
      await prisma.template.create({ data: { name: `${sequenceName} - ${step}`, sequenceName, segment, messageType: idx === 0 ? "resource_follow_up" : "newsletter", jurisdiction: segment === "existing_business_contracts" ? "NJ" : "NY", subject: `${segment === "existing_business_contracts" ? "" : "ATTORNEY ADVERTISING: "}${resource} for {{businessName}}`, bodyMarkdown: `Hello {{contactName}},\n\nWe prepared an educational ${resource}. It is intended to help business owners organize questions for counsel and spot topics that may merit review. It does not assess your specific facts or provide legal advice.\n\nYou can request the checklist or schedule a consultation after conflict review.\n\nAdvertising Classification: educational legal advertising.\n${footer(version)}`, lockedLegalLanguage: disclaimer, editableFields: { personalNote: "Non-legal personalization only" }, status: "approved", approvedAt: new Date(), approvedById: admin.id, version } });
    }
  }

  await prisma.template.create({ data: { name: "Referral partner education - issue spotting", sequenceName: "referral partner education", segment: "referral_partner", messageType: "newsletter", jurisdiction: "OTHER", subject: "Small-business legal issue-spotting resources", bodyMarkdown: `Hello {{contactName}},\n\nWe share educational issue-spotting resources with CPAs, bookkeepers, branding agencies, web agencies, insurance brokers, lenders, and fractional CFOs. The goal is mutual education, not referral fees.\n\nIf useful, we can compare notes on common formation, contract, and brand-protection questions that business clients ask.\n${footer(1)}`, lockedLegalLanguage: disclaimer, editableFields: { partnerType: "CPA/bookkeeper/agency/broker/lender/fractional CFO" }, status: "approved", approvedAt: new Date(), approvedById: admin.id, version: 1 } });
  await prisma.template.create({ data: { name: "Blocked sample - unsupported guarantee", segment: "founder", messageType: "newsletter", jurisdiction: "NY", subject: "We are the best and results guaranteed", bodyMarkdown: `You have a case and you need a lawyer.\n${footer(1)}`, lockedLegalLanguage: disclaimer, editableFields: {}, status: "draft", version: 1 } });

  const resources = [
    ["Founder Legal Checklist", "founder"], ["Contract Red Flags for Service Businesses", "existing_business_contracts"], ["Brand and Trademark Readiness Checklist", "trademark_brand"]
  ] as const;
  for (const [title, segment] of resources) {
    const resource = await prisma.resource.create({ data: { title, segment, markdownContent: `# ${title}\n\n- Gather business records.\n- List questions for attorney review.\n- Identify deadlines and counterparties.\n\nThis checklist is educational and does not evaluate your facts.`, status: "approved", approvedAt: new Date(), approvedById: admin.id, disclaimer, versionHistory: [{ version: 1, approvedBy: admin.email, approvedAt: new Date().toISOString() }] } });
    await prisma.landingPage.upsert({ where: { segment }, update: { resourceId: resource.id }, create: { segment, headline: title, bodyMarkdown: "Download an educational checklist and, if appropriate, start intake for conflict review.", resourceId: resource.id, bookingCta: "Request a consultation after conflict review.", disclaimer, intakeTrigger: "resource_download" } });
  }
}

main().finally(() => prisma.$disconnect());
