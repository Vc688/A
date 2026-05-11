export interface CalendarBooking { leadId: string; preferredTime?: string; summary: string }
export interface CalendarResult { providerEventId: string; status: "held" | "scheduled" }
export interface CalendarProvider { holdConsultation(input: CalendarBooking): Promise<CalendarResult> }

export class MockCalendarProvider implements CalendarProvider {
  async holdConsultation(input: CalendarBooking): Promise<CalendarResult> {
    return { providerEventId: `mock_calendar_${input.leadId}`, status: "held" };
  }
}

export function getCalendarProvider(): CalendarProvider {
  const provider = process.env.CALENDAR_PROVIDER ?? "mock";
  if (provider !== "mock") throw new Error(`Calendar provider ${provider} is configured but no adapter is installed. Add Google Calendar or Calendly adapter in lib/providers/calendar.ts.`);
  return new MockCalendarProvider();
}
