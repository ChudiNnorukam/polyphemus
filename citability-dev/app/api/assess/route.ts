import { NextRequest, NextResponse } from "next/server"
import { upsertLeadInAttio } from "@/lib/attio"

export async function POST(req: NextRequest) {
  try {
    const body = await req.json()
    const { name, email, website, phone, answers, score, freeText } = body

    if (!name || !email) {
      return NextResponse.json({ error: "Name and email are required" }, { status: 400 })
    }

    const tier: "high" | "medium" | "low" =
      score >= 8 ? "high" : score >= 5 ? "medium" : "low"

    // Push to Attio as a People record + note
    let attioId: string | undefined
    try {
      attioId = await upsertLeadInAttio({
        name,
        email,
        website: website || undefined,
        phone: phone || undefined,
        score,
        total: 10,
        tier,
        role:        answers?.role        || undefined,
        frustration: answers?.frustration || undefined,
        tried:       answers?.tried       || undefined,
        goal:        answers?.goal        || undefined,
        solution:    answers?.solution    || undefined,
        freeText:    freeText             || undefined,
      })
    } catch (attioErr) {
      console.error("Attio upsert failed:", attioErr)
      // Don't block the user — still send the email below
    }

    // Email notification via Resend
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
            subject: `New lead: ${name} (${tier.toUpperCase()}, ${score}/10) — ${website || "no site"}`,
            html: `
              <h2>New Assessment Lead</h2>
              <table style="border-collapse:collapse;font-family:monospace;font-size:14px">
                <tr><td style="padding:4px 12px 4px 0;color:#666">Name</td><td><strong>${name}</strong></td></tr>
                <tr><td style="padding:4px 12px 4px 0;color:#666">Email</td><td>${email}</td></tr>
                <tr><td style="padding:4px 12px 4px 0;color:#666">Website</td><td>${website || "—"}</td></tr>
                <tr><td style="padding:4px 12px 4px 0;color:#666">Phone</td><td>${phone || "—"}</td></tr>
                <tr><td style="padding:4px 12px 4px 0;color:#666">Score</td><td><strong>${score}/10</strong> (${tier.toUpperCase()})</td></tr>
                <tr><td style="padding:4px 12px 4px 0;color:#666">Goal</td><td>${answers?.goal || "—"}</td></tr>
                <tr><td style="padding:4px 12px 4px 0;color:#666">Role</td><td>${answers?.role || "—"}</td></tr>
                <tr><td style="padding:4px 12px 4px 0;color:#666">Notes</td><td>${freeText || "—"}</td></tr>
              </table>
              ${attioId ? `<p style="font-size:12px;color:#999;margin-top:16px">Attio record: ${attioId}</p>` : ""}
            `,
          }),
        })
      } catch (emailErr) {
        console.error("Resend failed:", emailErr)
      }
    }

    return NextResponse.json({ success: true, id: attioId ?? crypto.randomUUID() })
  } catch (err) {
    console.error("Assessment submission error:", err)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}
