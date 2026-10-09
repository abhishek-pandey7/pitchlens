"""5 Hz tracking reconstructed from the event stream (§7, T-12).

The ball follows event geometry exactly: it is at `start` when an event happens and reaches `end` after
`duration_ms`, with a parabolic height for aerial passes. Players move towards their team's dynamic shape
(`pitch.team_shape`), the ball carrier chases the ball, pass recipients run to the pass destination and
pressing players close the carrier down. Speeds are capped at each player's personal top speed, so
distance, sprint and top-speed metrics computed from tracking stay physically plausible.

Tracking is stored as compressed numpy arrays (`tracking.npz`): ~30k frames x 22 players would be
~30 MB as JSONL per match, but only a few MB here.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from pitchlens.schema.common import Point3
from pitchlens.schema.events import (
    EventType,
    Height,
    MatchEvent,
    MatchMeta,
    Outcome,
    TrackedPlayer,
    TrackingFrame,
)

from .pitch import FORMATION_ANCHORS, XY, mirror, team_shape
from .rosters import FORMATION_STARTERS, League, Player

HZ = 5
TICK_MS = 1000 // HZ
SHAPE_LAG_S = 2.0  # team shapes follow a smoothed ball position, not every touch
REPOSITION_S = 1.0  # off-ball players close the gap to their shape slot over ~1 s

ON_BALL = {
    EventType.PASS,
    EventType.CARRY,
    EventType.DRIBBLE,
    EventType.SHOT,
    EventType.POSSESSION_CHANGE,
    EventType.BALL_RECOVERY,
    EventType.INTERCEPTION,
    EventType.CLEARANCE,
    EventType.THROW_IN,
    EventType.GOAL_KICK,
    EventType.CORNER,
    EventType.FREE_KICK,
    EventType.SAVE,
}
TRAVEL = {EventType.PASS, EventType.THROW_IN, EventType.GOAL_KICK, EventType.CORNER, EventType.FREE_KICK}


@dataclass
class Tracking:
    match_id: str
    ms: np.ndarray  # (T,) int64 match_ms of each frame
    period: np.ndarray  # (T,) int8
    ball: np.ndarray  # (T, 3) float32
    player_ids: list[str]
    xy: np.ndarray  # (T, N, 2) float32, NaN while a player is off the pitch

    def __len__(self) -> int:
        return len(self.ms)

    def frame(self, i: int) -> TrackingFrame:
        bx, by, bz = (float(v) for v in self.ball[i])
        players = [
            TrackedPlayer(player_id=pid, x=round(float(x), 2), y=round(float(y), 2))
            for pid, (x, y) in zip(self.player_ids, self.xy[i], strict=True)
            if not math.isnan(x)
        ]
        return TrackingFrame(
            match_id=self.match_id,
            match_ms=int(self.ms[i]),
            ball=Point3(x=round(bx, 2), y=round(by, 2), z=round(bz, 2)),
            players=players,
        )

    def index_at(self, match_ms: int) -> int:
        """Index of the latest frame at or before `match_ms` (clamped to the first frame)."""
        return max(0, int(np.searchsorted(self.ms, match_ms, side="right")) - 1)

    def save(self, path: Path) -> Path:
        np.savez_compressed(
            path,
            match_id=np.array(self.match_id),
            ms=self.ms,
            period=self.period,
            ball=self.ball,
            player_ids=np.array(self.player_ids),
            xy=self.xy,
        )
        return path

    @classmethod
    def load(cls, path: Path) -> Tracking:
        with np.load(path) as data:
            return cls(
                match_id=str(data["match_id"]),
                ms=data["ms"],
                period=data["period"],
                ball=data["ball"],
                player_ids=[str(p) for p in data["player_ids"]],
                xy=data["xy"],
            )


class BallPath:
    """Piecewise-linear ball trajectory (x, y, z) built from event geometry."""

    def __init__(self, events: list[MatchEvent]) -> None:
        t: list[float] = []
        x: list[float] = []
        y: list[float] = []
        z: list[float] = []

        def add(ms: float, px: float, py: float, pz: float = 0.0) -> None:
            # A later event that happens before the previous flight finished interrupts it: drop the
            # planned remainder of that trajectory so the ball is where the event says it is.
            while t and t[-1] > ms:
                for arr in (t, x, y, z):
                    arr.pop()
            t.append(ms)
            x.append(px)
            y.append(py)
            z.append(pz)

        for e in events:
            if e.start is None:
                continue
            add(e.match_ms, e.start.x, e.start.y)
            if e.end is not None and e.duration_ms:
                end_ms = e.match_ms + e.duration_ms
                if e.height is Height.HIGH:
                    length = math.hypot(e.end.x - e.start.x, e.end.y - e.start.y)
                    add(
                        e.match_ms + e.duration_ms / 2,
                        (e.start.x + e.end.x) / 2,
                        (e.start.y + e.end.y) / 2,
                        min(0.12 * length, 18.0),
                    )
                add(end_ms, e.end.x, e.end.y)
        self.t = np.array(t, dtype=np.float64)
        self.x = np.array(x)
        self.y = np.array(y)
        self.z = np.array(z)

    def at(self, ms: np.ndarray | float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        return (np.interp(ms, self.t, self.x), np.interp(ms, self.t, self.y), np.interp(ms, self.t, self.z))


@dataclass
class _Side:
    club_id: str
    is_home: bool
    players: list[Player]  # on-pitch, slot-aligned
    anchors: list[XY]
    slots: list
    press: float
    width: float


def generate_tracking(meta: MatchMeta, events: list[MatchEvent], league: League) -> Tracking:
    ball = BallPath(events)
    clubs = {
        meta.home.club_id: league.club(meta.home.club_id),
        meta.away.club_id: league.club(meta.away.club_id),
    }
    sides = {
        cid: _Side(
            club_id=cid,
            is_home=cid == meta.home.club_id,
            players=club.starting_xi(),
            anchors=list(FORMATION_ANCHORS[club.formation]),
            slots=list(FORMATION_STARTERS[club.formation]),
            press=club.style.press_intensity,
            width=club.style.width,
        )
        for cid, club in clubs.items()
    }
    lookup = {p.player_id: p for club in clubs.values() for p in club.players}
    player_ids = sorted(lookup)
    col = {pid: i for i, pid in enumerate(player_ids)}

    windows = []
    start_ms = 0
    for e in events:
        if e.type is EventType.PERIOD_START:
            start_ms = e.match_ms
        elif e.type is EventType.PERIOD_END:
            windows.append((e.period, start_ms, e.match_ms))
    ticks = [(p, ms) for p, lo, hi in windows for ms in range(lo, hi + 1, TICK_MS)]
    n_ticks, n_players = len(ticks), len(player_ids)
    ms_arr = np.array([ms for _, ms in ticks], dtype=np.int64)
    bx, by, bz = ball.at(ms_arr.astype(np.float64))
    xy = np.full((n_ticks, n_players, 2), np.nan, dtype=np.float32)

    event_ms = [e.match_ms for e in events]
    pos: dict[str, np.ndarray] = {}
    possessor = meta.home.club_id
    carrier: str | None = None
    pass_target: tuple[str, XY, int] | None = None  # recipient, destination (abs), arrival ms
    pressers: dict[str, int] = {}  # player -> ms until which they close down the ball
    ei = 0
    dt = TICK_MS / 1000
    prev_period = 0
    shape_ref = np.array([52.5, 34.0])

    for ti, (period, ms) in enumerate(ticks):
        # Apply every event up to this tick.
        upto = bisect.bisect_right(event_ms, ms)
        while ei < upto:
            e = events[ei]
            ei += 1
            if e.type is EventType.POSSESSION_CHANGE and e.team_id:
                possessor = e.team_id
            if e.type is EventType.SUBSTITUTION and e.team_id and e.player_id and e.related_player_id:
                side = sides[e.team_id]
                i = next(k for k, p in enumerate(side.players) if p.player_id == e.player_id)
                side.players[i] = lookup[e.related_player_id]
                pos[e.related_player_id] = pos.pop(e.player_id, np.array([52.5, 1.0]))
            if e.type is EventType.CARD and e.outcome in (Outcome.RED, Outcome.SECOND_YELLOW) and e.team_id:
                side = sides[e.team_id]
                i = next(k for k, p in enumerate(side.players) if p.player_id == e.player_id)
                del side.players[i], side.anchors[i], side.slots[i]
                pos.pop(e.player_id or "", None)
            if e.type is EventType.PRESSURE and e.player_id:
                pressers[e.player_id] = e.match_ms + 1500
            if e.type in ON_BALL and e.player_id:
                carrier = e.player_id
                pass_target = None
                if e.type in TRAVEL and e.recipient_id and e.end and e.duration_ms:
                    # While the ball travels nobody carries it; the intended recipient runs onto it.
                    carrier = None
                    pass_target = (e.recipient_id, (e.end.x, e.end.y), e.match_ms + e.duration_ms)
            if e.type is EventType.SHOT:
                carrier = None
        if pass_target and ms >= pass_target[2]:
            carrier, pass_target = pass_target[0], None

        ball_abs: XY = (float(bx[ti]), float(by[ti]))
        new_period = period != prev_period
        prev_period = period
        if new_period:
            shape_ref = np.array(ball_abs)
        else:
            shape_ref = shape_ref + (np.array(ball_abs) - shape_ref) * min(1.0, dt / SHAPE_LAG_S)
        ref_abs: XY = (float(shape_ref[0]), float(shape_ref[1]))
        for side in sides.values():
            attacks_pos = side.is_home == (period == 1)

            def to_frame(p: XY, attacks_pos: bool = attacks_pos) -> XY:
                return p if attacks_pos else mirror(p)

            targets = team_shape(
                side.anchors,
                side.slots,
                to_frame(ref_abs),
                in_possession=side.club_id == possessor,
                press=side.press,
                width=side.width,
            )
            for player, target_tf in zip(side.players, targets, strict=True):
                pid = player.player_id
                target = np.array(to_frame(target_tf), dtype=np.float64)
                urgent = False
                if pid == carrier:
                    target, urgent = np.array(ball_abs), True
                elif pass_target and pid == pass_target[0]:
                    target, urgent = np.array(pass_target[1]), True
                elif pressers.get(pid, -1) >= ms:
                    target, urgent = np.array(ball_abs) + np.array([0.8, 0.0]), True
                current = pos.get(pid)
                if current is None or new_period:
                    pos[pid] = target
                else:
                    delta = target - current
                    dist = float(np.hypot(*delta))
                    top = player.top_speed_kmh / 3.6
                    if urgent:
                        speed = top * 0.97
                    else:
                        speed = min(dist / REPOSITION_S, top * (0.75 if dist > 15 else 0.6))
                    step = min(dist, speed * dt)
                    if dist > 1e-6:
                        pos[pid] = current + delta / dist * step
                xy[ti, col[pid]] = pos[pid]

    return Tracking(
        match_id=meta.match_id,
        ms=ms_arr,
        period=np.array([p for p, _ in ticks], dtype=np.int8),
        ball=np.stack([bx, by, bz], axis=1).astype(np.float32),
        player_ids=player_ids,
        xy=xy,
    )
