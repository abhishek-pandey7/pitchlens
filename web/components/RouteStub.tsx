import Link from "next/link";

type RouteStubProps = {
  title: string;
  audience: string;
  description: string;
};

/** Placeholder shell for routes that are not built yet; keeps navigation working end to end. */
export function RouteStub({ title, audience, description }: RouteStubProps) {
  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 px-4 py-12">
      <Link href="/" className="text-sm text-muted hover:text-foreground">
        ← PitchLens
      </Link>
      <p className="text-xs font-semibold uppercase tracking-widest text-accent">{audience}</p>
      <h1 className="text-3xl font-semibold">{title}</h1>
      <p className="text-muted">{description}</p>
    </main>
  );
}
