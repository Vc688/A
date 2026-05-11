import { NextResponse } from "next/server";
import PDFDocument from "pdfkit";
import { prisma } from "@/lib/prisma";

export async function GET(_: Request, { params }: { params: { id: string } }) {
  const resource = await prisma.resource.findUniqueOrThrow({ where: { id: params.id } });
  if (resource.status !== "approved") return NextResponse.json({ error: "Resource is not approved for publication." }, { status: 403 });
  const doc = new PDFDocument({ margin: 48 });
  const chunks: Buffer[] = [];
  doc.on("data", (chunk) => chunks.push(chunk));
  doc.fontSize(18).text(resource.title);
  doc.moveDown().fontSize(10).text(`Version ${resource.version}`);
  doc.moveDown().fontSize(12).text(resource.markdownContent.replace(/[#*_`]/g, ""));
  doc.moveDown().fontSize(9).text(resource.disclaimer);
  doc.end();
  await new Promise((resolve) => doc.on("end", resolve));
  return new NextResponse(Buffer.concat(chunks), { headers: { "content-type": "application/pdf", "content-disposition": `attachment; filename="${resource.title.replace(/\W+/g, "-")}.pdf"` } });
}
