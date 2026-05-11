import type { Role, UserLike } from "./types";

export const attorneyOnlyActions = [
  "approve_template",
  "approve_campaign",
  "approve_resource",
  "create_compliance_override",
  "clear_conflict",
  "release_outbound_message"
] as const;

export type AttorneyOnlyAction = (typeof attorneyOnlyActions)[number];
export const privilegedRoles: Role[] = ["admin", "attorney"];

export function canPerform(user: UserLike, action: AttorneyOnlyAction): boolean {
  void action;
  return privilegedRoles.includes(user.role);
}

export function assertCanPerform(user: UserLike, action: AttorneyOnlyAction): void {
  if (!canPerform(user, action)) {
    throw new Error(`Role ${user.role} cannot ${action}; attorney or admin review is required.`);
  }
}
