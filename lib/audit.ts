import { prisma } from "./prisma";

export async function audit(actorId: string | null, action: string, entityType: string, entityId: string, metadata: unknown = {}) {
  return prisma.auditLog.create({ data: { actorId, action, entityType, entityId, metadata: metadata as object } });
}
