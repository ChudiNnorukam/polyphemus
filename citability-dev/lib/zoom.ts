/**
 * Zoom Server-to-Server OAuth meeting creation.
 * Required env vars:
 *   ZOOM_ACCOUNT_ID
 *   ZOOM_CLIENT_ID
 *   ZOOM_CLIENT_SECRET
 */

export interface ZoomMeeting {
  joinUrl: string
  startUrl: string
  meetingId: string
  password: string
  startTime: Date
  endTime: Date
}

async function getAccessToken(): Promise<string> {
  const accountId = process.env.ZOOM_ACCOUNT_ID
  const clientId = process.env.ZOOM_CLIENT_ID
  const clientSecret = process.env.ZOOM_CLIENT_SECRET

  if (!accountId || !clientId || !clientSecret) {
    throw new Error("Zoom env vars missing: ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET")
  }

  const credentials = Buffer.from(`${clientId}:${clientSecret}`).toString("base64")
  const res = await fetch(
    `https://zoom.us/oauth/token?grant_type=account_credentials&account_id=${accountId}`,
    {
      method: "POST",
      headers: {
        Authorization: `Basic ${credentials}`,
        "Content-Type": "application/x-www-form-urlencoded",
      },
    }
  )

  if (!res.ok) {
    const err = await res.text()
    throw new Error(`Zoom token error ${res.status}: ${err}`)
  }

  const data = await res.json()
  return data.access_token as string
}

/** Returns the next business day at 10:00 AM ET (15:00 UTC). */
export function getNextConsultSlot(durationMinutes = 30): { start: Date; end: Date } {
  const now = new Date()
  // At least 24 hours out
  const candidate = new Date(now.getTime() + 24 * 60 * 60 * 1000)

  // Skip weekends
  while (candidate.getDay() === 0 || candidate.getDay() === 6) {
    candidate.setDate(candidate.getDate() + 1)
  }

  // 10:00 AM ET = 15:00 UTC (using EST/UTC-5 as base)
  candidate.setUTCHours(15, 0, 0, 0)

  // If that slot has already passed after the 24h shift, move to next weekday
  if (candidate <= now) {
    candidate.setDate(candidate.getDate() + 1)
    while (candidate.getDay() === 0 || candidate.getDay() === 6) {
      candidate.setDate(candidate.getDate() + 1)
    }
    candidate.setUTCHours(15, 0, 0, 0)
  }

  const end = new Date(candidate.getTime() + durationMinutes * 60 * 1000)
  return { start: candidate, end }
}

export async function createZoomMeeting(opts: {
  topic: string
  agenda: string
  leadName: string
  durationMinutes?: number
}): Promise<ZoomMeeting> {
  const duration = opts.durationMinutes ?? 30
  const { start, end } = getNextConsultSlot(duration)

  const token = await getAccessToken()

  const res = await fetch("https://api.zoom.us/v2/users/me/meetings", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      topic: opts.topic,
      type: 2, // scheduled
      start_time: start.toISOString().replace(".000Z", "Z"),
      duration,
      agenda: opts.agenda,
      settings: {
        host_video: true,
        participant_video: true,
        join_before_host: false,
        waiting_room: true,
        auto_recording: "none",
        email_notification: true,
      },
    }),
  })

  if (!res.ok) {
    const err = await res.text()
    throw new Error(`Zoom meeting creation error ${res.status}: ${err}`)
  }

  const data = await res.json()
  return {
    joinUrl: data.join_url as string,
    startUrl: data.start_url as string,
    meetingId: String(data.id),
    password: (data.password as string) ?? "",
    startTime: start,
    endTime: end,
  }
}
