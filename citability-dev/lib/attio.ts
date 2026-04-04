const ATTIO_API = "https://api.attio.com/v2"
const ATTIO_KEY = process.env.ATTIO_API_KEY!

async function attioFetch(path: string, method: string, body: unknown) {
  const res = await fetch(`${ATTIO_API}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${ATTIO_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`Attio ${method} ${path} → ${res.status}: ${err}`)
  }
  return res.json()
}

export interface AssessmentData {
  name: string
  email: string
  website?: string
  phone?: string
  score: number
  total: number
  tier: "high" | "medium" | "low"
  role?: string
  frustration?: string
  tried?: string
  goal?: string
  solution?: string
  freeText?: string
}

export async function upsertLeadInAttio(data: AssessmentData) {
  const [firstName, ...rest] = data.name.trim().split(" ")
  const lastName = rest.join(" ") || ""

  // 1. Upsert person matched on email
  const personRes = await attioFetch("/objects/people/records", "PUT", {
    data: {
      values: {
        name: [{ first_name: firstName, last_name: lastName }],
        email_addresses: [{ email_address: data.email }],
        ...(data.phone ? { phone_numbers: [{ phone_number: data.phone }] } : {}),
      },
    },
    matching_attribute: "email_addresses",
  })

  const personId = personRes.data?.id?.record_id
  if (!personId) throw new Error("No record_id returned from Attio upsert")

  // 2. Add a note with full assessment summary
  const tierEmoji = data.tier === "high" ? "🔥" : data.tier === "medium" ? "⚡" : "📋"
  const noteContent = [
    `${tierEmoji} citability.dev Assessment — Score: ${data.score}/${data.total} (${data.tier.toUpperCase()})`,
    "",
    `Website: ${data.website || "not provided"}`,
    `Goal: ${data.goal || "—"}`,
    `Role: ${data.role || "—"}`,
    `Frustration: ${data.frustration || "—"}`,
    `Tried: ${data.tried || "—"}`,
    `Preferred solution: ${data.solution || "—"}`,
    ...(data.freeText ? ["", `Notes: ${data.freeText}`] : []),
  ].join("\n")

  await attioFetch("/notes", "POST", {
    data: {
      parent_object: "people",
      parent_record_id: personId,
      title: `citability.dev scan — ${data.score}/${data.total}`,
      content: noteContent,
    },
  })

  return personId
}
