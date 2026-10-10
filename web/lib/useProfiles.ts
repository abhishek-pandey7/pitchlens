"use client";

import { useEffect, useState } from "react";
import { api } from "./api";
import type { Profile } from "./types";

export function useProfiles(): Profile[] {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  useEffect(() => {
    api.profiles().then(setProfiles).catch(() => setProfiles([]));
  }, []);
  return profiles;
}
