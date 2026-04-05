import { NextRequest, NextResponse } from "next/server"
import crypto from "crypto"
import { createZoomMeeting } from "@/lib/zoom"
import { generateIcs, icsToBase64 } from "@/lib/calendar"

const OWNER_EMAIL = "hello@citability.dev"
const OWNER_NAME = "Chudi Nnorukam"

function verifySignature(payload: string, signature: string, secret: string): boolean {
  const hmac = crypto.createHmac("sha256", secret)
  const digest = hmac.update(payload).digest("hex")
  return crypto.timingSafeEqual(Buffer.from(digest), Buffer.from(signature))
}

function isFullAudit(productName: string): boolean {
  return productName.toLowerCase().includes("audit") || productName.toLowerCase().includes("strategy")
}

export async function POST(req: NextRequest) {
  const secret = process.env.LEMONSQUEEZY_WEBHOOK_SECRET
  if (!secret) {
    console.error("LEMONSQUEEZY_WEBHOOK_SECRET not set")
    return NextResponse.json({ error: "Webhook secret not configured" }, { status: 500 })
  }

  const signature = req.headers.get("x-signature") ?? ""
  const rawBody = await req.text()

  if (!verifySignature(rawBody, signature, secret)) {
    return NextResponse.json({ error: "Invalid signature" }, { status: 401 })
  }

  const event = JSON.parse(rawBody)
  const eventName: string = event?.meta?.event_name ?? ""

  if (eventName !== "order_created") {
    return NextResponse.json({ received: true })
  }

  const order = event?.data?.attributes
  const customerEmail: string = order?.user_email ?? ""
  const customerName: string = order?.user_name ?? ""
  const orderTotal: number = order?.total ?? 0
  const orderId: string = String(event?.data?.id ?? "")
  const productName: string = order?.first_order_item?.product_name ?? "Quick Report"

  if (!customerEmail) {
    console.error("No customer email in order payload")
    return NextResponse.json({ error: "No customer email" }, { status: 400 })
  }

  const firstName = customerName.split(" ")[0] || customerName

  // 1. Schedule Zoom meeting for Full Audit orders
  let meeting: { joinUrl: string; startUrl: string; startTime: Date; endTime: Date; password: string } | null = null
  let icsAttachment: { filename: string; content: string } | null = null

  if (isFullAudit(productName)) {
    try {
      meeting = await createZoomMeeting({
        topic: `AI Visibility Strategy Call: ${customerName}`,
        agenda: `citability.dev Full Audit + Strategy Call for ${customerEmail}. Order: ${orderId}`,
        leadName: customerName,
        durationMinutes: 30,
      })

      const ics = generateIcs({
        uid: orderId,
        title: "AI Visibility Strategy Call - citability.dev",
        description: [
          `Hi ${firstName},`,
          ``,
          `Your Full Audit + Strategy Call is scheduled.`,
          `We will review your complete AI visibility audit and build your implementation roadmap.`,
          ``,
          `Join link: ${meeting.joinUrl}`,
          ...(meeting.password ? [`Password: ${meeting.password}`] : []),
        ].join("\n"),
        location: meeting.joinUrl,
        startTime: meeting.startTime,
        endTime: meeting.endTime,
        organizer: { name: OWNER_NAME, email: OWNER_EMAIL },
        attendee: { name: customerName, email: customerEmail },
      })
      icsAttachment = { filename: "citability-strategy-call.ics", content: icsToBase64(ics) }
    } catch (zoomErr) {
      console.error("Zoom meeting creation failed for Full Audit order:", zoomErr)
    }
  }

  // 2. Push buyer to Attio
  let attioPersonId: string | undefined
  try {
    const ATTIO_API = "https://api.attio.com/v2"
    const attioKey = process.env.ATTIO_API_KEY
    if (attioKey) {
      const [first, ...rest] = customerName.trim().split(" ")
      const last = rest.join(" ") || ""

      const personRes = await fetch(`${ATTIO_API}/objects/people/records`, {
        method: "PUT",
        headers: { Authorization: `Bearer ${attioKey}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          data: {
            values: {
              name: [{ first_name: first, last_name: last }],
              email_addresses: [{ email_address: customerEmail }],
            },
          },
          matching_attribute: "email_addresses",
        }),
      })

      if (personRes.ok) {
        const personData = await personRes.json()
        attioPersonId = personData.data?.id?.record_id

        if (attioPersonId) {
          const noteLines = [
            `Product: ${productName}`,
            `Amount: $${(orderTotal / 100).toFixed(2)}`,
            `Order ID: ${orderId}`,
            `Status: PAID, awaiting delivery`,
            ...(meeting ? [`Meeting: ${meeting.joinUrl}`] : []),
          ]

          await fetch(`${ATTIO_API}/notes`, {
            method: "POST",
            headers: { Authorization: `Bearer ${attioKey}`, "Content-Type": "application/json" },
            body: JSON.stringify({
              data: {
                parent_object: "people",
                parent_record_id: attioPersonId,
                title: `Purchase: ${productName} ($${(orderTotal / 100).toFixed(0)})`,
                format: "plaintext",
                content: noteLines.join("\n"),
              },
            }),
          })
        }
      }
    }
  } catch (err) {
    console.error("Attio push failed:", err)
  }

  const resendKey = process.env.RESEND_API_KEY

  // 3. Confirmation email to customer
  if (resendKey) {
    try {
      const meetingHtml = meeting
        ? `
          <div style="margin:16px 0;padding:16px;background:#f0fdf4;border-left:4px solid #16a34a;border-radius:4px">
            <strong style="color:#15803d">Strategy call scheduled</strong><br>
            <span style="font-size:13px;color:#166534">
              ${meeting.startTime.toLocaleDateString("en-US", { weekday: "long", year: "numeric", month: "long", day: "numeric" })}
              at 10:00 AM ET
            </span><br>
            <a href="${meeting.joinUrl}" style="color:#16a34a;font-size:13px">${meeting.joinUrl}</a>
            ${meeting.password ? `<br><span style="font-size:12px;color:#166534">Password: ${meeting.password}</span>` : ""}
          </div>
          <p style="color:#475569;margin-bottom:16px">A calendar invite is attached. Add it now so you don't miss it.</p>`
        : ""

      const deliveryNote = isFullAudit(productName)
        ? `Your full audit PDF and implementation roadmap will be in your inbox before the call.`
        : `I'll run your full AI visibility audit and have the PDF in your inbox within 48 hours.`

      await fetch("https://api.resend.com/emails", {
        method: "POST",
        headers: { Authorization: `Bearer ${resendKey}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          from: `Chudi at citability.dev <${OWNER_EMAIL}>`,
          to: customerEmail,
          subject: isFullAudit(productName)
            ? "Your Full Audit + Strategy Call is confirmed"
            : "Your citability.dev Quick Report: order received",
          html: `
            <div style="font-family:system-ui,sans-serif;max-width:560px;margin:0 auto;color:#0f172a">
              <p style="font-size:18px;font-weight:600;margin-bottom:8px">Got it, ${firstName}.</p>
              <p style="color:#475569;margin-bottom:24px">
                Order #${orderId} confirmed. ${deliveryNote}
              </p>
              ${meetingHtml}
              <p style="color:#475569;margin-bottom:8px">Your report includes:</p>
              <ul style="color:#475569;padding-left:20px;margin-bottom:24px">
                <li>Live AI visibility test (20 queries across ChatGPT, Perplexity, Claude)</li>
                <li>Live AI citability test (20 queries checking for direct citations)</li>
                <li>Recommendability analysis</li>
                <li>Scores and benchmarks vs. similar sites</li>
                ${isFullAudit(productName) ? "<li>Written implementation roadmap (5-7 prioritized actions)</li><li>Competitor comparison (your site vs. 2 competitors)</li>" : "<li>Top 3 actionable fixes</li>"}
              </ul>
              <p style="color:#475569;margin-bottom:4px">
                Reply here with your website URL if you haven't already. That is all I need to get started.
              </p>
              <p style="color:#94a3b8;font-size:13px;margin-top:32px">
                Chudi<br>
                <a href="https://citability.dev" style="color:#00d4d8">citability.dev</a>
              </p>
            </div>
          `,
          ...(icsAttachment ? { attachments: [icsAttachment] } : {}),
        }),
      })
    } catch (err) {
      console.error("Customer confirmation email failed:", err)
    }

    // 4. Owner alert
    try {
      const meetingAlert = meeting
        ? `<p style="font-family:monospace;font-size:13px;color:#15803d;margin-top:12px">Meeting scheduled: ${meeting.joinUrl}</p>`
        : ""

      await fetch("https://api.resend.com/emails", {
        method: "POST",
        headers: { Authorization: `Bearer ${resendKey}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          from: `citability.dev <notifications@citability.dev>`,
          to: OWNER_EMAIL,
          subject: `New order: ${customerName}, ${productName} ($${(orderTotal / 100).toFixed(0)})`,
          html: `
            <h2 style="font-family:monospace">New Purchase</h2>
            <table style="border-collapse:collapse;font-family:monospace;font-size:14px">
              <tr><td style="padding:4px 12px 4px 0;color:#666">Customer</td><td><strong>${customerName}</strong></td></tr>
              <tr><td style="padding:4px 12px 4px 0;color:#666">Email</td><td>${customerEmail}</td></tr>
              <tr><td style="padding:4px 12px 4px 0;color:#666">Product</td><td>${productName}</td></tr>
              <tr><td style="padding:4px 12px 4px 0;color:#666">Amount</td><td><strong>$${(orderTotal / 100).toFixed(2)}</strong></td></tr>
              <tr><td style="padding:4px 12px 4px 0;color:#666">Order ID</td><td>${orderId}</td></tr>
            </table>
            ${meetingAlert}
            <p style="font-family:monospace;font-size:13px;color:#666;margin-top:16px">
              ${isFullAudit(productName)
                ? `Deliver audit PDF before the scheduled call. Run /citability report for ${customerEmail}.`
                : `Run /citability report for ${customerEmail} and reply within 48h.`
              }
            </p>
          `,
          ...(icsAttachment ? { attachments: [icsAttachment] } : {}),
        }),
      })
    } catch (err) {
      console.error("Owner alert email failed:", err)
    }
  }

  return NextResponse.json({ received: true })
}
