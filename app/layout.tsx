import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = { title: "Legal Outreach & Intake", description: "Compliance-first client outreach and intake system" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body><main className="mx-auto max-w-7xl p-6">{children}</main></body></html>;
}
