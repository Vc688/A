import { prisma } from "./prisma";
import type { Role, UserLike } from "./types";

export async function currentUserFromHeaders(headers: Headers): Promise<UserLike> {
  const userId = headers.get("x-user-id");
  if (userId) {
    const user = await prisma.user.findUnique({ where: { id: userId } });
    if (user) return { id: user.id, role: user.role as Role, name: user.name, email: user.email };
  }
  const role = (headers.get("x-demo-role") ?? "intake_staff") as Role;
  return { id: "demo", role, name: "Demo User" };
}
