import { assertCanPerform } from "./authz";
import { evaluateCompliance } from "./compliance";
import { getEmailProvider } from "./providers/email";
import type { UserLike } from "./types";

export function approveEntity(actor: UserLike, action: "approve_template" | "approve_campaign" | "approve_resource" | "clear_conflict") {
  assertCanPerform(actor, action);
  return { status: "approved", approvedAt: new Date(), approvedById: actor.id };
}

export async function releaseMessage(actor: UserLike, message: any) {
  assertCanPerform(actor, "release_outbound_message");
  const result = evaluateCompliance({ lead: message.lead, template: message.template, campaign: message.campaign, messageType: message.template.messageType, finalSubject: message.finalSubject ?? message.subjectPreview, finalBody: message.finalBody ?? message.bodyPreview, overrides: message.overrides ?? [] });
  if (!result.ok) throw new Error(`Message blocked: ${result.reasons.join("; ")}`);
  const provider = getEmailProvider();
  return provider.send({ to: message.lead.email, subject: message.finalSubject ?? message.subjectPreview, body: message.finalBody ?? message.bodyPreview, metadata: { messageId: message.id, campaignId: message.campaignId ?? "" } });
}
