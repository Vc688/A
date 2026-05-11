export interface EmailPayload { to: string; subject: string; body: string; metadata?: Record<string, string> }
export interface EmailResult { providerMessageId: string; accepted: boolean }
export interface EmailProvider { send(payload: EmailPayload): Promise<EmailResult> }

export class MockEmailProvider implements EmailProvider {
  public sent: EmailPayload[] = [];
  async send(payload: EmailPayload): Promise<EmailResult> {
    this.sent.push(payload);
    return { providerMessageId: `mock_${Date.now()}_${this.sent.length}`, accepted: true };
  }
}

export function getEmailProvider(): EmailProvider {
  const provider = process.env.EMAIL_PROVIDER ?? "mock";
  if (provider !== "mock") throw new Error(`Email provider ${provider} is configured but no adapter is installed. Add Gmail or SendGrid adapter in lib/providers/email.ts.`);
  return new MockEmailProvider();
}
