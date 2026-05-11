export type Role = "admin" | "attorney" | "outreach_manager" | "intake_staff";
export type Segment = "founder" | "existing_business_contracts" | "trademark_brand" | "referral_partner" | "newsletter_only";
export type ConsentStatus = "unknown" | "opted_in" | "opted_out" | "existing_relationship";
export type RelationshipStatus = "none" | "existing_client" | "former_client" | "referral_partner" | "professional_contact";
export type ConflictStatus = "not_started" | "needs_review" | "cleared" | "conflict_found" | "declined";
export type ApprovalStatus = "draft" | "pending" | "approved" | "rejected";
export type MessageType = "cold_outreach" | "newsletter" | "resource_follow_up" | "transactional" | "legal_administrative";
export type Jurisdiction = "NY" | "NJ" | "OTHER";

export interface UserLike { id: string; role: Role; name?: string; email?: string }
export interface LeadLike {
  id: string; email: string; state: string; segment: Segment; doNotContact: boolean; businessName?: string | null; contactName?: string;
  consentStatus: ConsentStatus; relationshipStatus: RelationshipStatus; unsubscribedAt?: Date | string | null;
}
export interface TemplateLike {
  id: string; status: ApprovalStatus; subject: string; bodyMarkdown: string; lockedLegalLanguage?: string;
  version: number; messageType: MessageType; jurisdiction: Jurisdiction; containsTestimonial?: boolean; documentedNyException?: boolean;
}
export interface CampaignLike { id: string; status: ApprovalStatus; messageType: MessageType; segment: Segment }
export interface OverrideLike { targetType: string; targetId: string; reason: string; createdById: string; createdAt: Date | string }
