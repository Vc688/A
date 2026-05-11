import { notFound } from "next/navigation";
import { prisma } from "@/lib/prisma";

export default async function LandingPage({ params }: { params: { segment: string } }) {
  const page = await prisma.landingPage.findUnique({ where: { segment: params.segment as any } }).catch(() => null);
  if (!page) notFound();
  const resource = page.resourceId ? await prisma.resource.findUnique({ where: { id: page.resourceId } }) : null;
  return <div className="mx-auto max-w-3xl space-y-6">
    <section className="card"><h1 className="text-3xl font-bold">{page.headline}</h1><p className="mt-3 whitespace-pre-wrap text-slate-700">{page.bodyMarkdown}</p></section>
    <section className="card"><h2 className="text-xl font-semibold">Resource download</h2>{resource?.status === "approved" ? <a className="btn mt-3 inline-block" href={`/api/resources/${resource.id}/download`}>Download {resource.title}</a> : <p className="text-sm text-amber-700">Resource pending attorney/admin approval.</p>}</section>
    <section className="card"><h2 className="text-xl font-semibold">Next step</h2><p>{page.bookingCta}</p><a className="btn mt-3 inline-block" href="/intake">Start intake for conflict review</a></section>
    <p className="text-xs text-slate-500">{page.disclaimer}</p>
  </div>;
}
