/**
 * Generates an RFC 5545 .ics calendar invite.
 * Returns the raw ICS string. Base64-encode it for email attachments.
 */

function fmtDate(d: Date): string {
  return d.toISOString().replace(/[-:.]/g, "").slice(0, 15) + "Z"
}

function escapeIcs(str: string): string {
  return str.replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\n/g, "\\n")
}

export interface IcsOptions {
  uid: string
  title: string
  description: string
  location: string
  startTime: Date
  endTime: Date
  organizer: { name: string; email: string }
  attendee: { name: string; email: string }
}

export function generateIcs(opts: IcsOptions): string {
  const now = fmtDate(new Date())

  return [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//citability.dev//AI Visibility Consultation//EN",
    "CALSCALE:GREGORIAN",
    "METHOD:REQUEST",
    "BEGIN:VEVENT",
    `UID:${opts.uid}@citability.dev`,
    `DTSTAMP:${now}`,
    `DTSTART:${fmtDate(opts.startTime)}`,
    `DTEND:${fmtDate(opts.endTime)}`,
    `ORGANIZER;CN="${escapeIcs(opts.organizer.name)}":mailto:${opts.organizer.email}`,
    `ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;RSVP=TRUE;CN="${escapeIcs(opts.attendee.name)}":mailto:${opts.attendee.email}`,
    `SUMMARY:${escapeIcs(opts.title)}`,
    `DESCRIPTION:${escapeIcs(opts.description)}`,
    `LOCATION:${escapeIcs(opts.location)}`,
    "STATUS:CONFIRMED",
    "SEQUENCE:0",
    "BEGIN:VALARM",
    "TRIGGER:-PT60M",
    "ACTION:DISPLAY",
    "DESCRIPTION:Reminder",
    "END:VALARM",
    "END:VEVENT",
    "END:VCALENDAR",
  ].join("\r\n")
}

export function icsToBase64(ics: string): string {
  return Buffer.from(ics, "utf-8").toString("base64")
}
