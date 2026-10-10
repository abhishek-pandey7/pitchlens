import asyncio

from pitchlens.narrate.mock import MockProvider
from pitchlens.narrate.narrator import Narrator
from pitchlens.narrate.templates import NarrationContext
from pitchlens.narrate.worker import NarrationWorker
from pitchlens.personalize.ranker import rank
from pitchlens.render.cues import CueScheduler, build_cue
from pitchlens.schema.insights import Entities, Evidence, Insight, InsightType
from pitchlens.schema.overlays import Region
from pitchlens.schema.profiles import FocusMetric, Persona, PlayerFocus, Profile

CTX = NarrationContext(
    match_id="m", home_id="NHR", away_id="SVC", team_names={"NHR": "Northbridge", "SVC": "Southvale"},
    player_names={"NHR-10": "Reyes", "SVC-06": "Okafor"}, score=(1, 0),
)  # fmt: skip


def insight(
    kind: InsightType = InsightType.PRESSURE_CHANGE,
    *,
    salience: float = 0.4,
    teams: tuple[str, ...] = ("SVC",),
    players: tuple[str, ...] = (),
    metric: str = "ppda",
    match_ms: int = 600_000,
    key: str = "pressure_change.press_intensified",
    iid: str = "i-1",
) -> Insight:
    return Insight(
        insight_id=iid, match_id="m", match_ms=match_ms, clock_ms=match_ms, period=1, type=kind,
        salience=salience, entities=Entities(teams=list(teams), players=list(players)), headline_key=key,
        evidence=[Evidence(metric=metric, team=teams[0] if teams else None, value=0.5)],
    )  # fmt: skip


# ---------------------------------------------------------------- ranker
def test_personas_weigh_the_same_moment_differently() -> None:
    press = insight(salience=0.4)
    casual = rank(press, Profile(profile_id="c", persona=Persona.CASUAL))
    tactician = rank(press, Profile(profile_id="t", persona=Persona.TACTICIAN))
    assert tactician.personal_salience > casual.personal_salience
    assert tactician.feed and not casual.feed


def test_club_player_focus_metric_and_player_focus_boosts() -> None:
    base = Profile(profile_id="b", persona=Persona.CASUAL)
    moment = insight(InsightType.PASS_OF_NOTE, salience=0.25, players=("SVC-06",), metric="pass_difficulty")
    plain = rank(moment, base)
    club = rank(moment, base.model_copy(update={"favorite_clubs": ["SVC"]}))
    player = rank(moment, base.model_copy(update={"favorite_players": ["SVC-06"]}))
    focus = rank(moment, base.model_copy(update={"focus_metric": FocusMetric.PASS_DIFFICULTY}))
    follow = rank(
        moment, base.model_copy(update={"player_focus_mode": PlayerFocus(enabled=True, player_id="SVC-06")})
    )
    assert (
        plain.personal_salience < club.personal_salience < player.personal_salience < follow.personal_salience
    )
    assert focus.personal_salience > plain.personal_salience
    assert club.reasons == ["favorite_club"] and "player_focus" in follow.reasons
    assert not plain.feed and follow.feed and follow.overlay


def test_spoiler_safe_flags_score_revealing_moments() -> None:
    goal = insight(InsightType.GOAL, salience=0.9, key="goal.go_ahead")
    assert rank(goal, Profile(profile_id="s", spoiler_safe=True)).spoiler
    assert not rank(goal, Profile(profile_id="n")).spoiler
    assert not rank(insight(salience=0.9), Profile(profile_id="s", spoiler_safe=True)).spoiler


# ---------------------------------------------------------------- cue scheduler
def cue(kind: InsightType, ms: int, *, players: tuple[str, ...] = (), iid: str = "i"):
    return build_cue(
        insight(kind, match_ms=ms, players=players, iid=iid), Profile(profile_id="p"), headline="h"
    )


def test_regions_preempt_by_priority() -> None:
    scheduler = CueScheduler(max_per_min=10)
    assert scheduler.admit(cue(InsightType.PRESSURE_CHANGE, 1000, iid="a"))  # lower third, priority 50
    assert not scheduler.admit(cue(InsightType.RHYTHM_BREAK, 2000, iid="b"))  # same region, lower priority
    assert scheduler.admit(cue(InsightType.GOAL, 3000, iid="c"))  # goal preempts
    assert scheduler.admit(cue(InsightType.PASS_OF_NOTE, 3500, iid="d"))  # different region is free
    assert scheduler.admit(cue(InsightType.RHYTHM_BREAK, 20_000, iid="e"))  # region free again later


def test_rate_limit_spares_goals() -> None:
    scheduler = CueScheduler(max_per_min=2)
    regions_free_ms = [0, 10_000]
    assert all(scheduler.admit(cue(InsightType.PASS_OF_NOTE, ms, iid=str(ms))) for ms in regions_free_ms)
    assert not scheduler.admit(cue(InsightType.PRESSURE_CHANGE, 20_000, iid="x"))  # third in a minute
    assert scheduler.admit(cue(InsightType.GOAL, 25_000, iid="g"))  # goals always make air
    assert scheduler.admit(cue(InsightType.PRESSURE_CHANGE, 75_000, iid="y"))  # window has moved on


def test_player_tags_anchor_to_the_player() -> None:
    tag = cue(InsightType.SPRINT_THRESHOLD, 1000, players=("NHR-10",))
    assert tag.region is Region.PLAYER_ANCHOR and tag.anchor and tag.anchor.player_id == "NHR-10"
    orphan = cue(InsightType.SPRINT_THRESHOLD, 1000)
    assert orphan.region is Region.LOWER_THIRD and orphan.anchor is None


# ---------------------------------------------------------------- narration worker
class SlowMock(MockProvider):
    async def generate(self, request):  # type: ignore[override]
        await asyncio.sleep(0.01)
        return await super().generate(request)


def goal_insight(iid: str = "i-goal") -> Insight:
    return Insight(
        insight_id=iid, match_id="m", match_ms=600_000, clock_ms=600_000, period=1, type=InsightType.GOAL,
        salience=0.9, entities=Entities(teams=["NHR"], players=["NHR-10"]), headline_key="goal.go_ahead",
        evidence=[Evidence(metric="score", value="1-0")],
    )  # fmt: skip


async def test_worker_shares_calls_across_fans_with_the_same_settings() -> None:
    provider = SlowMock()
    worker = NarrationWorker(Narrator(provider))
    fans = [Profile(profile_id=f"f{i}", persona=Persona.CASUAL, language="es") for i in range(5)]
    results = await asyncio.gather(
        *(worker.narrate(goal_insight(), f, CTX, personal_salience=0.9) for f in fans)
    )
    assert provider.calls == 1 and worker.stats.deduplicated == 4
    assert [r.profile_id for r in results] == [f.profile_id for f in fans]
    again = await worker.narrate(goal_insight(), fans[0], CTX, personal_salience=0.9)
    assert again is not None and worker.stats.cache_hits == 1 and provider.calls == 1
    english = await worker.narrate(goal_insight(), Profile(profile_id="en"), CTX, personal_salience=0.9)
    assert english is not None and english.lang == "en" and provider.calls == 2


async def test_worker_sheds_low_salience_under_load() -> None:
    worker = NarrationWorker(Narrator(SlowMock()), max_pending=1, shed_below=0.5)
    fan = Profile(profile_id="f")
    busy = asyncio.ensure_future(worker.narrate(goal_insight("a"), fan, CTX, personal_salience=0.9))
    await asyncio.sleep(0)
    assert await worker.narrate(goal_insight("b"), fan, CTX, personal_salience=0.2) is None
    assert await worker.narrate(goal_insight("c"), fan, CTX, personal_salience=0.9) is not None
    await busy
    assert worker.stats.shed == 1
