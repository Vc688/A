import type { LeadLike, TemplateLike } from "./types";

export function renderTemplate(template: TemplateLike, lead: LeadLike & { contactName?: string; businessName?: string | null }, personalization: Record<string, string> = {}) {
  const values = { contactName: lead.contactName ?? "there", businessName: lead.businessName ?? "your business", ...personalization };
  const replace = (text: string) => text.replace(/{{\s*(\w+)\s*}}/g, (_, key) => values[key as keyof typeof values] ?? "");
  return { subject: replace(template.subject), body: replace(template.bodyMarkdown) };
}
