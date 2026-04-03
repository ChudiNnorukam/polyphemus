import { ImageResponse } from "next/og"

export const runtime = "edge"
export const alt = "citability.dev - AI Visibility Auditing"
export const size = { width: 1200, height: 630 }
export const contentType = "image/png"

export default async function Image() {
  return new ImageResponse(
    (
      <div
        style={{
          background: "linear-gradient(135deg, #0b1120 0%, #0f1729 50%, #0b1120 100%)",
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "60px 80px",
          fontFamily: "system-ui, sans-serif",
        }}
      >
        {/* Top accent line */}
        <div
          style={{
            width: 60,
            height: 4,
            background: "#2dd4bf",
            borderRadius: 2,
            marginBottom: 32,
          }}
        />

        {/* Title */}
        <div
          style={{
            fontSize: 56,
            fontWeight: 700,
            color: "#f0f6fc",
            lineHeight: 1.2,
            marginBottom: 20,
          }}
        >
          citability.dev
        </div>

        {/* Subtitle */}
        <div
          style={{
            fontSize: 28,
            color: "#94a3b8",
            lineHeight: 1.5,
            maxWidth: 800,
          }}
        >
          Measure whether AI systems can find you, recommend you, and cite you.
        </div>

        {/* Bottom stats row */}
        <div
          style={{
            display: "flex",
            gap: 40,
            marginTop: 48,
          }}
        >
          {[
            { n: "10", label: "Verified Checks" },
            { n: "3", label: "AI Platforms Tested" },
            { n: "0", label: "Made-Up Scores" },
          ].map((stat) => (
            <div
              key={stat.label}
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 4,
              }}
            >
              <div style={{ fontSize: 36, fontWeight: 700, color: "#2dd4bf" }}>
                {stat.n}
              </div>
              <div style={{ fontSize: 14, color: "#64748b", textTransform: "uppercase", letterSpacing: 1 }}>
                {stat.label}
              </div>
            </div>
          ))}
        </div>
      </div>
    ),
    { ...size }
  )
}
