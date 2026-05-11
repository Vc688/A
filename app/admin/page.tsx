import { prisma } from "@/lib/prisma";

export default async function AdminPage() {
  const [leadsBySegment, blockedMessages, pendingTemplates, consultations, sentMessages] = await Promise.all([
    prisma.lead.groupBy({ by: ["segment"], _count: true }).catch(() => []),
    prisma.outboundMessage.count({ where: { status: "blocked" } }).catch(() => 0),
    prisma.template.count({ where: { status: { in: ["draft", "pending"] } } }).catch(() => 0),
    prisma.consultation.count().catch(() => 0),
    prisma.outboundMessage.count({ where: { status: "sent" } }).catch(() => 0)
  ]);
  return <div className="space-y-6">
    <h1 className="text-3xl font-bold">Admin dashboard</h1>
    <div className="grid gap-4 md:grid-cols-4">
      <Metric title="Compliance-blocked messages" value={blockedMessages} />
      <Metric title="Pending attorney approvals" value={pendingTemplates} />
      <Metric title="Scheduled/requested consultations" value={consultations} />
      <Metric title="Sent messages" value={sentMessages} />
    </div>
    <section className="card"><h2 className="text-xl font-semibold">Leads by segment</h2><ul className="mt-3 space-y-2">{leadsBySegment.map((row: any) => <li key={row.segment} className="flex justify-between border-b py-2"><span>{row.segment}</span><span>{row._count}</span></li>)}</ul></section>
    <section className="card"><h2 className="text-xl font-semibold">Audit exports</h2><div className="mt-3 flex flex-wrap gap-2">{["audit-logs","sent-messages","recipients","approvals","campaigns"].map((type) => <a key={type} className="btn" href={`/api/exports/${type}`}>{type}</a>)}</div></section>
    <section className="card"><h2 className="text-xl font-semibold">Filters</h2><p className="text-sm text-slate-600">Operational endpoints support filtering by state, source, segment, status, and date as the next UI refinement; exported records preserve auditability.</p></section>
  </div>;
}
function Metric({ title, value }: { title: string; value: number }) { return <div className="card"><p className="text-sm text-slate-500">{title}</p><p className="mt-2 text-3xl font-bold">{value}</p></div>; }
