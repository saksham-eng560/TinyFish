import { ImageResponse } from "next/og";

export const alt = "HireFlow: swipe right on internships, and the agent applies. Powered by TinyFish";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

/** The social preview (1200×630), drawn at build time in the brand's orange and black. */
export default function OpengraphImage() {
  const orange = "#FF6A00"; // fills and large shapes (2.9:1 on white, so never small text)
  const orangeText = "#D95500"; // text on white (large text only)
  const ink = "#0A0A0A";
  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", justifyContent: "space-between",
        background: "#FFFFFF", padding: "64px 72px", fontFamily: "sans-serif", color: ink, position: "relative" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
          <svg width="64" height="64" viewBox="0 0 32 32">
            <rect width="32" height="32" rx="7.5" fill={orange} />
            <path d="M10.25 8.5v15M21.75 8.5v15" fill="none" stroke="#0A0A0A" strokeWidth="4.75" strokeLinecap="round" />
            <path d="M10.25 18.75L21.75 13.25" fill="none" stroke="#0A0A0A" strokeWidth="4" strokeLinecap="round" />
          </svg>
          <div style={{ display: "flex", fontSize: 44 }}><span style={{ fontWeight: 800 }}>Hire</span><span>Flow</span></div>
        </div>
        <div style={{ display: "flex", flexDirection: "column" }}>
          <div style={{ display: "flex", fontSize: 92, fontWeight: 800, lineHeight: 1.02, letterSpacing: -3 }}>Swipe right.</div>
          <div style={{ display: "flex", fontSize: 92, fontWeight: 800, lineHeight: 1.02, letterSpacing: -3, color: orangeText }}>We apply.</div>
          <div style={{ display: "flex", marginTop: 28, fontSize: 32, color: "#57534E", maxWidth: 640 }}>
            Internships found, tailored, filled and tracked — only for the jobs you keep.
          </div>
        </div>
        {/* a swipe card on the right, like the deck */}
        <div style={{ position: "absolute", right: 80, top: 150, width: 300, height: 360, display: "flex", borderRadius: 24,
          border: "2px solid #D6D3D1", background: "#FFF4EB", transform: "rotate(7deg)" }} />
        <div style={{ position: "absolute", right: 96, top: 140, width: 300, height: 360, display: "flex", flexDirection: "column",
          borderRadius: 24, border: "2px solid #D6D3D1", background: "#FFFFFF", padding: 28, transform: "rotate(-3deg)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
            <div style={{ display: "flex", fontSize: 16, color: "#78716C", letterSpacing: 2 }}>BENGALURU</div>
            <div style={{ display: "flex", fontSize: 54, color: orangeText }}>86</div>
          </div>
          <div style={{ display: "flex", fontSize: 30, marginTop: 20, lineHeight: 1.1 }}>Backend Engineering Intern</div>
          <div style={{ display: "flex", fontSize: 20, marginTop: 8, color: "#78716C" }}>Summer 2027</div>
          <div style={{ display: "flex", marginTop: "auto", gap: 10 }}>
            <div style={{ display: "flex", flex: 1, justifyContent: "center", padding: "12px 0", borderRadius: 12,
              border: "2px solid #A8A29E", fontSize: 20 }}>Skip</div>
            <div style={{ display: "flex", flex: 1, justifyContent: "center", padding: "12px 0", borderRadius: 12,
              background: orange, color: ink, fontSize: 20 }}>Keep</div>
          </div>
        </div>
        <div style={{ display: "flex", gap: 14 }}>
          {["Find", "Swipe", "Apply & track", "Powered by TinyFish"].map((step) => (
            <div key={step} style={{ display: "flex", padding: "10px 24px", borderRadius: 999, border: `2px solid ${orange}`,
              color: orangeText, fontSize: 26, fontWeight: 700 }}>{step}</div>
          ))}
        </div>
      </div>
    ),
    size,
  );
}
