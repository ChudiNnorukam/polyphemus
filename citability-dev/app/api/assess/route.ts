import { NextRequest, NextResponse } from "next/server"
import { promises as fs } from "fs"
import path from "path"

export async function POST(req: NextRequest) {
  try {
    const body = await req.json()

    const { name, email, website, phone, answers, score, freeText } = body

    if (!name || !email) {
      return NextResponse.json({ error: "Name and email are required" }, { status: 400 })
    }

    // Determine qualification tier
    const q14 = answers?.q14 || "diy"
    const tier =
      score >= 8 ? "high" :
      score >= 5 ? "medium" : "low"

    const submission = {
      id: crypto.randomUUID(),
      timestamp: new Date().toISOString(),
      name,
      email,
      website: website || "",
      phone: phone || "",
      answers,
      score,
      tier,
      budgetSignal: q14,
      freeText: freeText || "",
      ip: req.headers.get("x-forwarded-for") || req.headers.get("x-real-ip") || "unknown",
    }

    // Store to JSON file (MVP - replace with DB later)
    const dataDir = path.join(process.cwd(), "data")
    await fs.mkdir(dataDir, { recursive: true })

    const filePath = path.join(dataDir, "submissions.json")
    let submissions: unknown[] = []
    try {
      const existing = await fs.readFile(filePath, "utf-8")
      submissions = JSON.parse(existing)
    } catch {
      // File doesn't exist yet
    }
    submissions.push(submission)
    await fs.writeFile(filePath, JSON.stringify(submissions, null, 2))

    // Send email notification via Resend (if API key configured)
    const resendKey = process.env.RESEND_API_KEY
    if (resendKey) {
      try {
        await fetch("https://api.resend.com/emails", {
          method: "POST",
          headers: {
            Authorization: `Bearer ${resendKey}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            from: "citability.dev <notifications@citability.dev>",
            to: "hello@citability.dev",
            subject: `New Assessment: ${name} (${tier.toUpperCase()}, ${q14})`,
            html: `
              <h2>New Assessment Submission</h2>
              <table style="border-collapse:collapse;font-family:monospace;font-size:14px">
                <tr><td style="padding:4px 12px 4px 0;color:#666">Name</td><td><strong>${name}</strong></td></tr>
                <tr><td style="padding:4px 12px 4px 0;color:#666">Email</td><td>${email}</td></tr>
                <tr><td style="padding:4px 12px 4px 0;color:#666">Website</td><td>${website || "not provided"}</td></tr>
                <tr><td style="padding:4px 12px 4px 0;color:#666">Phone</td><td>${phone || "not provided"}</td></tr>
                <tr><td style="padding:4px 12px 4px 0;color:#666">Score</td><td><strong>${score}/10</strong> (${tier.toUpperCase()})</td></tr>
                <tr><td style="padding:4px 12px 4px 0;color:#666">Budget Signal</td><td>${q14}</td></tr>
                <tr><td style="padding:4px 12px 4px 0;color:#666">Free Text</td><td>${freeText || "none"}</td></tr>
              </table>
              <br>
              <p style="font-size:12px;color:#999">Submission ID: ${submission.id}</p>
            `,
          }),
        })
      } catch (emailErr) {
        console.error("Email send failed:", emailErr)
        // Don't fail the request if email fails
      }
    }

    return NextResponse.json({ success: true, id: submission.id })
  } catch (err) {
    console.error("Assessment submission error:", err)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}
