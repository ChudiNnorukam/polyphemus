import { ImageResponse } from "next/og"

export const runtime = "edge"
export const size = { width: 180, height: 180 }
export const contentType = "image/png"

export default function AppleIcon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: 180,
          height: 180,
          background: "#0f1729",
          borderRadius: 36,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <div
          style={{
            fontSize: 110,
            fontWeight: 800,
            color: "#2dd4bf",
            fontFamily: "system-ui, sans-serif",
            lineHeight: 1,
          }}
        >
          c
        </div>
      </div>
    ),
    { ...size }
  )
}
