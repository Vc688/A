import Link from "next/link";

export default function Home() {
  return <div className="space-y-8">
    <section className="card">
      <p className="text-sm font-semibold uppercase tracking-wide text-amber-700">Attorney review required before production use</p>
      <h1 className="mt-2 text-4xl font-bold">Compliance-first outreach and intake for NY/NJ business law practices</h1>
      <p className="mt-4 max-w-3xl text-slate-600">Manage segmented leads, attorney-approved templates, human-reviewed outreach, conflict-check intake, resource downloads, and immutable audit trails without automated legal advice or automatic attorney-client relationship formation.</p>
      <div className="mt-6 flex gap-3"><Link className="btn" href="/admin">Admin dashboard</Link><Link className="btn" href="/intake">Client intake</Link></div>
    </section>
    <section className="grid gap-4 md:grid-cols-3">
      {["founder", "existing_business_contracts", "trademark_brand"].map((segment) => <Link key={segment} className="card hover:border-slate-400" href={`/landing/${segment}`}><h2 className="font-semibold">{segment.replaceAll("_", " ")}</h2><p className="text-sm text-slate-600">Approved-resource landing page with booking CTA and disclaimer.</p></Link>)}
    </section>
  </div>;
}
