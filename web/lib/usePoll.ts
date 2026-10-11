"use client";

import { useEffect, useState } from "react";

/** Polls `load` every `ms` while `key` is set; keeps the last good value through transient errors. */
export function usePoll<T>(key: string | null, load: () => Promise<T>, ms: number): T | null {
  const [value, setValue] = useState<{ key: string; data: T } | null>(null);

  useEffect(() => {
    if (!key) return;
    let alive = true;
    const tick = () =>
      load()
        .then((data) => alive && setValue({ key, data }))
        .catch(() => undefined);
    void tick();
    const timer = setInterval(tick, ms);
    return () => {
      alive = false;
      clearInterval(timer);
    };
    // `load` is recreated each render; the key and interval decide when to resubscribe.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, ms]);

  return value && value.key === key ? value.data : null;
}
