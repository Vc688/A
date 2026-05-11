export default function IntakePage() {
  return <form className="card mx-auto max-w-2xl space-y-4" method="post" action="/api/intake">
    <h1 className="text-2xl font-bold">Business legal intake</h1>
    <p className="text-sm text-slate-600">Submitting this form does not create an attorney-client relationship. The firm must complete a conflict check and agree in writing before representation begins.</p>
    {[
      ["leadId", "Lead ID"], ["businessType", "Business type"], ["state", "State"], ["legalNeedCategory", "Legal need category"], ["urgency", "Urgency"], ["opposingParties", "Opposing parties"], ["existingLawyerInvolvement", "Existing lawyer involvement"], ["trademarkOrBrandName", "Trademark or brand name"], ["contractType", "Contract type"], ["companyStatus", "Company status"], ["preferredConsultationTime", "Preferred consultation time"]
    ].map(([name, label]) => <label key={name} className="block"><span className="label">{label}</span><input className="input mt-1" name={name} required={["leadId","state","legalNeedCategory","urgency"].includes(name)} /></label>)}
    <button className="btn" type="submit">Submit for conflict review</button>
  </form>;
}
