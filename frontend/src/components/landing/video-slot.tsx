import { PipelineMotion } from "@/components/motion-graphics";
import { DEMO_VIDEO_URL, videoEmbed } from "@/lib/site";

/**
 * The 60-second walkthrough. Set NEXT_PUBLIC_DEMO_VIDEO_URL to a YouTube or Loom link, or to an .mp4/.webm
 * file; until then the slot shows the animated loop of what the agent does.
 */
export function VideoSlot() {
  const embed = videoEmbed(DEMO_VIDEO_URL);
  if (!embed) {
    return (
      <div className="rounded-2xl border bg-secondary/60 px-4 py-10 shadow-sm sm:px-10 sm:py-14">
        <p className="label-caps mb-8 text-center text-primary">One loop of the agent</p>
        <PipelineMotion />
      </div>
    );
  }
  return (
    <div className="overflow-hidden rounded-2xl border bg-card shadow-sm">
      <div className="relative aspect-video w-full">
        {embed.kind === "iframe" ? (
          <iframe src={embed.src} title="HireFlow in 60 seconds" loading="lazy" className="absolute inset-0 h-full w-full"
            allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture; fullscreen"
            referrerPolicy="strict-origin-when-cross-origin" allowFullScreen />
        ) : (
          <video src={embed.src} className="absolute inset-0 h-full w-full object-cover" autoPlay muted loop playsInline controls
            aria-label="HireFlow in 60 seconds" />
        )}
      </div>
    </div>
  );
}
