import { ImageResponse } from "next/og"

export const runtime = "edge"
export const size = { width: 32, height: 32 }
export const contentType = "image/png"

export default function Icon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: 32,
          height: 32,
          background: "#0f1729",
          borderRadius: 6,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <div
          style={{
            fontSize: 20,
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
