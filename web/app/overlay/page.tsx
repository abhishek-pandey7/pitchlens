/** Broadcast overlay: transparent 1920×1080 canvas, loaded as an OBS browser source. */
export default function OverlayPage() {
  return (
    <main className="overlay-root relative mx-auto aspect-video w-full max-w-[1920px]">
      <div className="absolute bottom-[8%] left-[5%] rounded-lg bg-surface/90 px-6 py-3 text-lg font-semibold shadow-lg">
        PitchLens overlay: waiting for match feed
      </div>
    </main>
  );
}
