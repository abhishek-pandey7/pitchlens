import Link from "next/link";
import { GoalHero } from "@/components/GoalHero";

// Who each view is for, in their words. The order follows a matchday: the fan first, the broadcast last.
const VIEWS = [
  { href: "/watch", title: "Watch", body: "Follow a match your way: your club, your player, your language, as much or as little detail as you like." },
  { href: "/compare", title: "Compare", body: "See the same moment told to two different fans, side by side." },
  { href: "/analyst", title: "Analyst", body: "Win probability, xG, momentum and pressure, with the evidence behind every insight." },
  { href: "/studio", title: "Studio", body: "Run the replay, approve what goes on air, read the agents' working and write the recaps." },
  { href: "/overlay", title: "Overlay", body: "Transparent broadcast graphics for OBS or a rendering partner." },
];

export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-14 px-4 py-14 sm:py-20">
      <header className="flex max-w-3xl flex-col gap-5">
        <p className="display text-2xl text-accent">PitchLens</p>
        <h1 className="display text-5xl sm:text-7xl">The game, explained for every fan.</h1>
        <p className="max-w-[60ch] text-lg text-muted">
          Live match events go in. What happened, why it matters and what it means for your club come out as
          commentary, graphics and recaps in six languages, checked against the numbers before anyone sees them.
        </p>
        <div className="flex flex-wrap items-center gap-4">
          <Link href="/watch" className="rounded-md bg-accent px-5 py-2.5 font-semibold text-white hover:bg-accent/90">
            Watch a match
          </Link>
          <Link href="/compare" className="font-semibold text-accent underline-offset-4 hover:underline">
            Compare two fans
          </Link>
        </div>
      </header>
      <GoalHero />
      <nav aria-label="Views">
        <ul className="grid gap-x-10 sm:grid-cols-2">
          {VIEWS.map((view) => (
            <li key={view.href} className="border-t border-surface-2">
              <Link href={view.href} className="group block py-4">
                <span className="text-lg font-semibold group-hover:text-accent">{view.title}</span>
                <span className="mt-1 block max-w-[52ch] text-muted">{view.body}</span>
              </Link>
            </li>
          ))}
        </ul>
      </nav>
      <footer className="text-sm text-muted">
        All data is synthetic. No real Premier League match data, players or trademarks are used.
      </footer>
    </main>
  );
}
