"""Fan profiles: an in-memory store with demo presets (Cosmos DB backs this in the cloud, T-72)."""

from __future__ import annotations

import uuid

from pitchlens.schema.profiles import FocusMetric, Persona, PlayerFocus, Profile, Verbosity

PRESETS: list[Profile] = [
    Profile(
        profile_id="p-casual-es",
        display_name="Lucía (Southvale fan)",
        persona=Persona.CASUAL,
        language="es",
        favorite_clubs=["SVC"],
        verbosity=Verbosity.SHORT,
        max_overlays_per_min=2,
        audio_commentary=True,
    ),
    Profile(
        profile_id="p-analyst-en",
        display_name="Analyst desk",
        persona=Persona.ANALYST,
        language="en",
        focus_metric=FocusMetric.PASS_DIFFICULTY,
        verbosity=Verbosity.LONG,
        max_overlays_per_min=6,
    ),
    Profile(
        profile_id="p-tactician-en",
        display_name="Tactics nerd",
        persona=Persona.TACTICIAN,
        language="en",
        focus_metric=FocusMetric.PPDA,
        verbosity=Verbosity.MEDIUM,
        max_overlays_per_min=4,
    ),
    Profile(
        profile_id="p-casual-en-nhr",
        display_name="Sam (Northbridge fan)",
        persona=Persona.CASUAL,
        language="en",
        favorite_clubs=["NHR"],
        player_focus_mode=PlayerFocus(enabled=True, player_id="NHR-10"),
    ),
    Profile(
        profile_id="p-casual-hi",
        display_name="Arjun",
        persona=Persona.CASUAL,
        language="hi",
        verbosity=Verbosity.SHORT,
    ),
    Profile(
        profile_id="p-casual-ar",
        display_name="Layla",
        persona=Persona.CASUAL,
        language="ar",
        verbosity=Verbosity.SHORT,
    ),
]


class ProfileStore:
    def __init__(self, presets: list[Profile] | None = None) -> None:
        self._profiles: dict[str, Profile] = {
            p.profile_id: p for p in (PRESETS if presets is None else presets)
        }

    def list(self) -> list[Profile]:
        return list(self._profiles.values())

    def get(self, profile_id: str) -> Profile | None:
        return self._profiles.get(profile_id)

    def create(self, profile: Profile | None = None, **fields: object) -> Profile:
        if profile is None:
            profile = Profile.model_validate({"profile_id": f"p-{uuid.uuid4().hex[:10]}", **fields})
        self._profiles[profile.profile_id] = profile
        return profile

    def put(self, profile: Profile) -> Profile:
        self._profiles[profile.profile_id] = profile
        return profile
