import { NextRequest, NextResponse } from "next/server"
import { upsertLeadInAttio } from "@/lib/attio"
import { generateReportPrompt, getIcpLabel, needsConsultation } from "@/lib/report-prompt"
import { createZoomMeeting, getNextConsultSlot } from "@/lib/zoom"
import { generateIcs, icsToBase64 } from "@/lib/calendar"

const OWNER_EMAIL = "hello@citability.dev"
const OWNER_NAME = "Chudi Nnorukam"

async function sendEmail(opts: {
  resendKey: string
  from: string
  to: string
  subject: string
  html: string
  attachments?: Array<{ filename: string; content: string }>
}) {
  await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${opts.resendKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      from: opts.from,
      to: opts.to,
      subject: opts.subject,
      html: opts.html,
      ...(opts.attachments ? { attachments: opts.attachments } : {}),
    }),
  })
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json()
    const { name, email, website, phone, answers, score, freeText } = body

    if (!name || !email) {
      return NextResponse.json({ error: "Name and email are required" }, { status: 400 })
    }

    const tier: "high" | "medium" | "low" =
      score >= 8 ? "high" : score >= 5 ? "medium" : "low"

    const role = answers?.role || "other"
    const goal = answers?.goal || "benchmark"
    const solution = answers?.solution || "report"

    // 1. Generate ICP report prompt
    const reportPrompt = generateReportPrompt({
      name,
      email,
      website: website || "",
      role,
      frustration: answers?.frustration || "unknown",
      tried: answers?.tried || "nothing",
      goal,
      solution,
      score,
      freeText: freeText || undefined,
    })
    const icpLabel = getIcpLabel({ role, goal })

    // 2. Schedule Zoom meeting (only for consult / done-for-you leads)
    let meeting: { joinUrl: string; startUrl: string; startTime: Date; endTime: Date; password: string } | null = null
    if (needsConsultation(solution)) {
      try {
        const durationMinutes = solution === "done_for_you" ? 60 : 30
        meeting = await createZoomMeeting({
          topic: `AI Visibility Consultation: ${name}`,
          agenda: `citability.dev consultation for ${website || email}. ICP: ${icpLabel}`,
          leadName: name,
          durationMinutes,
        })
      } catch (zoomErr) {
        console.error("Zoom meeting creation failed:", zoomErr)
        // Non-blocking, continue without meeting
      }
    }

    // 3. Upsert to Attio with meeting link + report prompt
    let attioId: string | undefined
    try {
      attioId = await upsertLeadInAttio({
        name, email,
        website: website || undefined,
        phone: phone || undefined,
        score, total: 10, tier,
        role, frustration: answers?.frustration, tried: answers?.tried,
        goal, solution, freeText: freeText || undefined,
        meetingLink: meeting?.joinUrl,
        reportPrompt,
      })
    } catch (attioErr) {
      console.error("Attio upsert failed:", attioErr)
    }

    // 4. Build calendar .ics if meeting was created
    let icsAttachment: { filename: string; content: string } | null = null
    if (meeting) {
      const ics = generateIcs({
        uid: attioId ?? crypto.randomUUID(),
        title: `AI Visibility Consultation - citability.dev`,
        description: [
          `Hi ${name},`,
          ``,
          `Looking forward to our AI visibility consultation.`,
          `We'll cover your citability.dev scan results and build a prioritised action plan for: ${goal}.`,
          ``,
          `Join link: ${meeting.joinUrl}`,
          ...(meeting.password ? [`Password: ${meeting.password}`] : []),
        ].join("\n"),
        location: meeting.joinUrl,
        startTime: meeting.startTime,
        endTime: meeting.endTime,
        organizer: { name: OWNER_NAME, email: OWNER_EMAIL },
        attendee: { name, email },
      })
      icsAttachment = { filename: "citability-consultation.ics", content: icsToBase64(ics) }
    }

    const resendKey = process.env.RESEND_API_KEY
    if (resendKey) {
      const meetingHtml = meeting
        ? `
          <div style="margin:16px 0;padding:16px;background:#f0fdf4;border-left:4px solid #16a34a;border-radius:4px">
            <strong style="color:#15803d">Meeting scheduled</strong><br>
            <span style="font-size:13px;color:#166534">
              ${meeting.startTime.toLocaleDateString("en-US", { weekday: "long", year: "numeric", month: "long", day: "numeric" })}
              at 10:00 AM ET
            </span><br>
            <a href="${meeting.joinUrl}" style="color:#16a34a;font-size:13px">${meeting.joinUrl}</a>
            ${meeting.password ? `<br><span style="font-size:12px;color:#166534">Password: ${meeting.password}</span>` : ""}
          </div>`
        : ""

      // Email to lead
      try {
        const leadSubject = meeting
          ? `Your citability.dev consultation is confirmed`
          : `Your citability.dev scan results are ready`

        await sendEmail({
          resendKey,
          from: `Chudi at citability.dev <${OWNER_EMAIL}>`,
          to: email,
          subject: leadSubject,
          html: `
            <p>Hi ${name},</p>
            <p>Thanks for completing the citability.dev assessment. Your infrastructure scan scored <strong>${score}/10</strong> (${tier.toUpperCase()}).</p>
            ${meetingHtml}
            ${meeting ? `<p>A calendar invite is attached. Add it to your calendar and I'll see you then.</p>` : ""}
            <p>In the meantime, <a href="https://citability.dev/results">view your full scan results</a>.</p>
            <p>Chudi<br><span style="color:#999;font-size:12px">citability.dev</span></p>
          `,
          ...(icsAttachment ? { attachments: [icsAttachment] } : {}),
        })
      } catch (emailErr) {
        console.error("Lead email failed:", emailErr)
      }

      // Email to owner (Chudi)
      try {
        await sendEmail({
          resendKey,
          from: `citability.dev <notifications@citability.dev>`,
          to: OWNER_EMAIL,
          subject: `New lead: ${name} (${tier.toUpperCase()}, ${score}/10), ${icpLabel}`,
          html: `
            <h2>New Assessment Lead</h2>
            <table style="border-collapse:collapse;font-family:monospace;font-size:13px">
              <tr><td style="padding:4px 12px 4px 0;color:#666">Name</td><td><strong>${name}</strong></td></tr>
              <tr><td style="padding:4px 12px 4px 0;color:#666">Email</td><td>${email}</td></tr>
              <tr><td style="padding:4px 12px 4px 0;color:#666">Website</td><td>${website || "-"}</td></tr>
              <tr><td style="padding:4px 12px 4px 0;color:#666">Phone</td><td>${phone || "-"}</td></tr>
              <tr><td style="padding:4px 12px 4px 0;color:#666">Score</td><td><strong>${score}/10</strong> (${tier.toUpperCase()})</td></tr>
              <tr><td style="padding:4px 12px 4px 0;color:#666">ICP</td><td>${icpLabel}</td></tr>
              <tr><td style="padding:4px 12px 4px 0;color:#666">Goal</td><td>${answers?.goal || "-"}</td></tr>
              <tr><td style="padding:4px 12px 4px 0;color:#666">Solution</td><td>${solution}</td></tr>
              <tr><td style="padding:4px 12px 4px 0;color:#666">Notes</td><td>${freeText || "-"}</td></tr>
            </table>
            ${meetingHtml}
            <hr style="margin:16px 0;border:none;border-top:1px solid #e5e7eb">
            <h3 style="font-family:monospace;font-size:13px;color:#374151">REPORT BRIEF</h3>
            <pre style="font-family:monospace;font-size:12px;background:#f9fafb;padding:12px;border-radius:4px;white-space:pre-wrap">${reportPrompt}</pre>
            ${attioId ? `<p style="font-size:11px;color:#9ca3af;margin-top:12px">Attio: ${attioId}</p>` : ""}
          `,
          ...(icsAttachment ? { attachments: [icsAttachment] } : {}),
        })
      } catch (emailErr) {
        console.error("Owner email failed:", emailErr)
      }
    }

    return NextResponse.json({
      success: true,
      id: attioId ?? crypto.randomUUID(),
      meetingScheduled: !!meeting,
      meetingUrl: meeting?.joinUrl ?? null,
    })
  } catch (err) {
    console.error("Assessment submission error:", err)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}
