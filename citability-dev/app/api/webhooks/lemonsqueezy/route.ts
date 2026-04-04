import { NextRequest, NextResponse } from "next/server"
import crypto from "crypto"

// Verify the webhook came from LemonSqueezy
function verifySignature(payload: string, signature: string, secret: string): boolean {
  const hmac = crypto.createHmac("sha256", secret)
  const digest = hmac.update(payload).digest("hex")
  return crypto.timingSafeEqual(Buffer.from(digest), Buffer.from(signature))
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
    // Acknowledge but don't process other event types
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

  // 1. Push buyer to Attio
  try {
    const ATTIO_API = "https://api.attio.com/v2"
    const attioKey = process.env.ATTIO_API_KEY
    if (attioKey) {
      const [firstName, ...rest] = customerName.trim().split(" ")
      const lastName = rest.join(" ") || ""

      const personRes = await fetch(`${ATTIO_API}/objects/people/records`, {
        method: "PUT",
        headers: {
          Authorization: `Bearer ${attioKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          data: {
            values: {
              name: [{ first_name: firstName, last_name: lastName }],
              email_addresses: [{ email_address: customerEmail }],
            },
          },
          matching_attribute: "email_addresses",
        }),
      })

      if (personRes.ok) {
        const personData = await personRes.json()
        const personId = personData.data?.id?.record_id

        if (personId) {
          await fetch(`${ATTIO_API}/notes`, {
            method: "POST",
            headers: {
              Authorization: `Bearer ${attioKey}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              data: {
                parent_object: "people",
                parent_record_id: personId,
                title: `💰 citability.dev Purchase — ${productName} ($${(orderTotal / 100).toFixed(0)})`,
                format: "plaintext",
                content: [
                  `Product: ${productName}`,
                  `Amount: $${(orderTotal / 100).toFixed(2)}`,
                  `Order ID: ${orderId}`,
                  `Status: PAID — awaiting report delivery`,
                ].join("\n"),
              },
            }),
          })
        }
      }
    }
  } catch (err) {
    console.error("Attio push failed:", err)
    // Non-fatal — still send emails
  }

  const resendKey = process.env.RESEND_API_KEY

  // 2. Send confirmation email to customer
  if (resendKey) {
    try {
      await fetch("https://api.resend.com/emails", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${resendKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          from: "Chudi at citability.dev <hello@citability.dev>",
          to: customerEmail,
          subject: "Your Citability Quick Report — received",
          html: `
            <div style="font-family:system-ui,sans-serif;max-width:560px;margin:0 auto;color:#0f172a">
              <p style="font-size:18px;font-weight:600;margin-bottom:8px">Got it, ${customerName.split(" ")[0]}.</p>
              <p style="color:#475569;margin-bottom:24px">
                Your Quick Report order (#${orderId}) came through. I'll run your full AI visibility audit
                and have the PDF report in your inbox within 48 hours.
              </p>
              <p style="color:#475569;margin-bottom:8px">Your report will include:</p>
              <ul style="color:#475569;padding-left:20px;margin-bottom:24px">
                <li>Live AI visibility test (20 queries across ChatGPT, Perplexity, Claude)</li>
                <li>Live AI citability test (20 queries checking for direct citations)</li>
                <li>Recommendability analysis</li>
                <li>Scores + benchmarks vs. similar sites</li>
                <li>Top 3 actionable fixes</li>
              </ul>
              <p style="color:#475569;margin-bottom:4px">
                Reply to this email with your website URL if you haven't already — that's all I need.
              </p>
              <p style="color:#94a3b8;font-size:13px;margin-top:32px">
                — Chudi<br>
                <a href="https://citability.dev" style="color:#00d4d8">citability.dev</a>
              </p>
            </div>
          `,
        }),
      })
    } catch (err) {
      console.error("Customer confirmation email failed:", err)
    }

    // 3. Alert yourself
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
          subject: `💰 New order: ${customerName} — ${productName} ($${(orderTotal / 100).toFixed(0)})`,
          html: `
            <h2 style="font-family:monospace">New Purchase</h2>
            <table style="border-collapse:collapse;font-family:monospace;font-size:14px">
              <tr><td style="padding:4px 12px 4px 0;color:#666">Customer</td><td><strong>${customerName}</strong></td></tr>
              <tr><td style="padding:4px 12px 4px 0;color:#666">Email</td><td>${customerEmail}</td></tr>
              <tr><td style="padding:4px 12px 4px 0;color:#666">Product</td><td>${productName}</td></tr>
              <tr><td style="padding:4px 12px 4px 0;color:#666">Amount</td><td><strong>$${(orderTotal / 100).toFixed(2)}</strong></td></tr>
              <tr><td style="padding:4px 12px 4px 0;color:#666">Order ID</td><td>${orderId}</td></tr>
            </table>
            <p style="font-family:monospace;font-size:13px;color:#666;margin-top:16px">
              Run /citability report for ${customerEmail} and reply within 48h.
            </p>
          `,
        }),
      })
    } catch (err) {
      console.error("Owner alert email failed:", err)
    }
  }

  return NextResponse.json({ received: true })
}
