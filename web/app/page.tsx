import Link from "next/link";

const ROUTES = [
  { href: "/watch", title: "Watch", body: "Personalized second screen: your persona, language, club and player." },
  { href: "/compare", title: "Compare", body: "Same moment, two fans. Side-by-side personalized experiences." },
  { href: "/overlay", title: "Overlay", body: "Transparent broadcast overlay for OBS or a rendering partner." },
  { href: "/analyst", title: "Analyst", body: "Win probability, xG, momentum, pressure and the evidence behind every insight." },
  { href: "/studio", title: "Studio", body: "Producer console: replay control, insight queue, agent trace, recaps." },
  { href: "/matchday", title: "Matchday", body: "Several live matches, cross-match alerts and a live table." },
];

export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-10 px-4 py-16">
      <header className="flex flex-col gap-3">
        <p className="text-xs font-semibold uppercase tracking-widest text-accent">PitchLens</p>
        <h1 className="text-4xl font-semibold sm:text-5xl">The game, explained for every fan.</h1>
        <p className="max-w-2xl text-muted">
          Synthetic football events in, explainable insights, live narratives, overlays and recaps out,
          personalized by persona, language, club and player.
        </p>
      </header>
      <nav className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {ROUTES.map((route) => (
          <Link
            key={route.href}
            href={route.href}
            className="rounded-xl border border-surface-2 bg-surface p-5 transition-colors hover:border-accent"
          >
            <h2 className="text-lg font-semibold">{route.title}</h2>
            <p className="mt-1 text-sm text-muted">{route.body}</p>
          </Link>
        ))}
      </nav>
      <footer className="text-xs text-muted">
        All data is synthetic. No real Premier League match data, players or trademarks are used.
      </footer>
    </main>
  );
}
