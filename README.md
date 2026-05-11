# Legal Client Outreach and Intake System

> **Lawyer review required before production use.** This application is a conservative workflow system for a small New York/New Jersey business law practice. It is not legal advice, does not create an attorney-client relationship, and must be reviewed by qualified counsel before any production outreach.

## What this app does

This is a full-stack Next.js/TypeScript application with PostgreSQL, Prisma, Tailwind, server-side API routes, role-based approvals, mock email/calendar providers, intake automation, and compliance-sensitive tests. It supports business formation, contracts, trademark/brand protection, referral-partner education, and newsletter/resource follow-up workflows.

The app intentionally prioritizes reliability, traceability, and legal-marketing controls over growth automation:

- No automatic legal advice.
- No automatic attorney-client relationship formation.
- No outcome guarantees.
- No website or social-network scraping.
- No v1 auto-send for cold targeted solicitation emails.
- Attorney/admin approval is required for templates, campaigns, resources, compliance overrides, conflict clearance, and outbound release.

## Stack

- Next.js App Router with TypeScript
- PostgreSQL with Prisma schema and migration
- Tailwind CSS
- Server-side API routes
- Vitest for compliance-sensitive logic
- Mock email and calendar provider interfaces with adapter seams for Gmail/Google Calendar, SendGrid, Calendly, or other lawful integrations

## Environment variables

Copy `.env.example` to `.env` and update values:

```env
DATABASE_URL="postgresql://postgres:postgres@localhost:5432/legal_outreach"
AUTH_SECRET="replace-with-a-long-random-secret"
APP_URL="http://localhost:3000"
EMAIL_PROVIDER="mock"
CALENDAR_PROVIDER="mock"
FIRM_NAME="Hudson Small Business Counsel"
FIRM_PRINCIPAL_OFFICE_ADDRESS="123 Main Street, New York, NY 10001"
FIRM_PHONE="(212) 555-0100"
RESPONSIBLE_ATTORNEY="Avery Cohen"
FIRM_JURISDICTION="New York and New Jersey"
```

## Setup

```bash
npm install
cp .env.example .env
npm run prisma:generate
npm run db:migrate
npm run db:seed
npm run dev
```

Open `http://localhost:3000`.

## Database migration steps

The initial Prisma migration is in `prisma/migrations/20260511000000_init/migration.sql`.

```bash
npm run db:migrate
```

For production deploys, use your platform's Prisma migration command, typically:

```bash
npx prisma migrate deploy
```

## Seed command

```bash
npm run db:seed
```

Seed data includes:

- Users for `admin`, `attorney`, and `outreach_manager`.
- Sample NY/NJ leads.
- A configurable prohibited-claims dictionary.
- Approved educational sequences for founder formation, contract risk review, trademark brand review, and referral partners.
- One intentionally unapproved/non-compliant template demonstrating the compliance blocker.
- Approved resource templates and landing pages.

## Test command

```bash
npm test
```

Tests cover:

- Unapproved templates cannot be sent.
- Do-not-contact blocks sending.
- New York messages require `ATTORNEY ADVERTISING` unless attorney exception/override exists.
- Prohibited claims block approval/sending.
- Non-attorneys cannot approve campaigns, templates, resources, overrides, or conflict clearance.
- Intake summaries avoid legal-advice language.
- Unsubscribe immediately prevents future campaign sends.
- Sent-message audit fields preserve final copy, subject, recipient, timestamp, template version, approver, and campaign ID.

## Local run command

```bash
npm run dev
```

## Sample CSV import format

See `data/sample-leads.csv`.

Required headers:

```csv
businessName,contactName,email,phone,website,state,source,segment,notes,consentStatus,relationshipStatus,doNotContact
North Star Studio LLC,Maya Founder,maya@example.test,212-555-0101,https://example.test,NY,event,founder,Asked for checklist,opted_in,none,false
```

Supported lead segments:

- `founder`
- `existing_business_contracts`
- `trademark_brand`
- `referral_partner`
- `newsletter_only`

## Roles and protected actions

Roles:

- `admin`
- `attorney`
- `outreach_manager`
- `intake_staff`

Only `admin` and `attorney` can:

- Approve templates.
- Approve campaigns.
- Approve resources.
- Create compliance overrides.
- Mark a lead as cleared for consultation.
- Release outbound messages.

## Compliance notes

The compliance module uses conservative New York and New Jersey defaults.

### New York defaults

For New York-targeted outbound messages promoting legal services, the system requires:

- Subject prefix: `ATTORNEY ADVERTISING`, unless an attorney/admin records a documented exception.
- Firm name.
- Principal office address.
- Phone number.
- Responsible attorney.
- Jurisdiction.
- Template version.
- Approval timestamp in persisted records.
- Preservation of final sent copy.

### New Jersey defaults

For New Jersey-targeted messages, the system:

- Requires an advertising classification field.
- Blocks misleading comparative claims.
- Blocks unsupported testimonials or guarantees.
- Requires attorney/admin approval for testimonial or endorsement language.

### All jurisdictions

The prohibited-claims dictionary is configurable and seeded with conservative defaults including:

- `guaranteed`
- `best`
- `top lawyer`
- `specialist`
- `expert`
- `we will win`
- `you have a case`
- `you need a lawyer`
- `results guaranteed`

The compliance gate also attempts to block text that applies law to recipient-specific facts. This is a safety control, not a substitute for attorney review.

## Outreach workflow

1. Import or create leads with segment, state, consent, relationship, and do-not-contact status.
2. Enrich leads only from user-provided fields and manual notes.
3. Use attorney-approved templates with locked legal language and editable non-legal personalization fields.
4. Evaluate compliance for state, segment, message type, approval status, labels, footer, template version, recipient status, and do-not-contact status.
5. Queue messages for human review.
6. Permit auto-send only for newsletters or resource follow-ups where the contact is opted in or has an existing relationship and the template has attorney/admin approval.
7. Preserve final sent copy and audit events.

## Intake workflow

The lead-facing intake form collects:

- Business type
- State
- Legal need category
- Urgency
- Opposing parties
- Existing lawyer involvement
- Trademark or brand name, if applicable
- Contract type, if applicable
- Company status
- Preferred consultation time

Conflict statuses:

- `not_started`
- `needs_review`
- `cleared`
- `conflict_found`
- `declined`

A legal consultation cannot be booked unless conflict status is `cleared` or an attorney/admin records an override. Pre-call summaries use only intake answers and lead metadata and list facts, requested help, possible service category, urgency, missing information, and conflict-check status.

## Resource automation

Seeded editable resources:

- Founder Legal Checklist
- Contract Red Flags for Service Businesses
- Brand and Trademark Readiness Checklist

Resources are markdown-backed, versioned, and downloadable as PDFs only after attorney/admin approval. Landing pages may serve only approved resources.

## Security notes

- Set a strong `AUTH_SECRET`.
- Place the app behind HTTPS in production.
- Use a managed PostgreSQL database with backups and encryption at rest.
- Limit admin routes to authenticated firm users.
- Review all provider adapters before enabling real email/calendar sends.
- Preserve audit logs according to the firm's retention policy.
- Treat CSV imports as untrusted input and review imported recipients before outreach.

## Production-readiness checklist

Before production use, a lawyer and technical owner should review:

- Advertising rules and disclaimers for each jurisdiction and message type.
- All template language and landing-page copy.
- Email provider unsubscribe behavior.
- Data retention and audit-log retention policies.
- Authentication and user provisioning.
- Calendar booking rules and conflict-check gates.
- Backups, monitoring, and incident response.
