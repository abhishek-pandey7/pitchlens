//! 5 Hz tracking reconstructed from the event stream: a line-for-line port of
//! `pitchlens/sim/tracking.py` (`BallPath` and `generate_tracking_py`).
//!
//! Python packs one match into plain arrays (`_pack` in `sim/tracking.py`): event columns, the two
//! sides (slot-aligned XI as player columns, anchors, slot units, press, width) and each player's top
//! speed. Everything that knows about rosters, formations and event types stays in Python; this module
//! only sees codes. Arithmetic is f64 in the Python order of operations; output is f32 as before.

use crate::shape::{mirror, py_min, team_shape, Unit, XY};

pub const TICK_MS: i64 = 200; // 5 Hz
const SHAPE_LAG_S: f64 = 2.0; // team shapes follow a smoothed ball position, not every touch
const REPOSITION_S: f64 = 1.0; // off-ball players close the gap to their shape slot over ~1 s

// Event kinds (`kind` column). Python maps `EventType` to these; every other type is OTHER.
pub const OTHER: u8 = 0;
pub const PERIOD_START: u8 = 1;
pub const PERIOD_END: u8 = 2;
pub const POSSESSION_CHANGE: u8 = 3;
pub const SUBSTITUTION: u8 = 4;
pub const CARD: u8 = 5;
pub const PRESSURE: u8 = 6;
pub const SHOT: u8 = 7;

// Event flags (`flags` column, a bit set).
pub const ON_BALL: u8 = 1; // type in tracking.ON_BALL
pub const TRAVEL: u8 = 2; // type in tracking.TRAVEL
pub const HIGH: u8 = 4; // height is Height.HIGH
pub const SENT_OFF: u8 = 8; // outcome is RED or SECOND_YELLOW

/// Team and player references: an index (side or player column), or one of these.
pub const NONE: i64 = -1; // None or "" in Python (falsy)
pub const UNKNOWN: i64 = -2; // a non-empty id this match doesn't know (truthy, matches nobody)

/// One match's events as columns, all of the same length.
pub struct Events<'a> {
    pub kind: &'a [u8],
    pub flags: &'a [u8],
    pub match_ms: &'a [i64],
    pub period: &'a [i64],
    pub team: &'a [i64],
    pub player: &'a [i64],
    pub related: &'a [i64],
    pub recipient: &'a [i64],
    pub duration_ms: &'a [i64], // 0 for None
    pub start: &'a [[f64; 2]],  // NaN for None
    pub end: &'a [[f64; 2]],    // NaN for None
}

impl Events<'_> {
    pub fn len(&self) -> usize {
        self.kind.len()
    }

    pub fn is_empty(&self) -> bool {
        self.kind.is_empty()
    }

    fn point(p: [f64; 2]) -> Option<XY> {
        if p[0].is_nan() {
            None
        } else {
            Some((p[0], p[1]))
        }
    }
}

/// One team: the on-pitch players (player columns), slot-aligned with anchors and units.
#[derive(Clone, Debug)]
pub struct Side {
    pub is_home: bool,
    pub players: Vec<usize>,
    pub anchors: Vec<XY>,
    pub units: Vec<Unit>,
    pub press: f64,
    pub width: f64,
}

#[derive(Debug, PartialEq)]
pub enum BuildError {
    Key(String),
    StopIteration(String),
    Value(String),
}

/// `BallPath`: a piecewise-linear ball trajectory (x, y, z) through the event geometry.
#[derive(Debug, Default)]
pub struct BallPath {
    pub t: Vec<f64>,
    pub x: Vec<f64>,
    pub y: Vec<f64>,
    pub z: Vec<f64>,
}

/// The ball-relevant part of one event.
#[derive(Clone, Copy, Debug)]
pub struct BallEvent {
    pub match_ms: i64,
    pub start: Option<XY>,
    pub end: Option<XY>,
    pub duration_ms: i64,
    pub high: bool,
}

impl BallPath {
    pub fn new(events: impl IntoIterator<Item = BallEvent>) -> BallPath {
        let mut path = BallPath::default();
        for e in events {
            let Some((sx, sy)) = e.start else { continue };
            path.add(e.match_ms as f64, sx, sy, 0.0);
            if let (Some((ex, ey)), true) = (e.end, e.duration_ms != 0) {
                let end_ms = e.match_ms + e.duration_ms;
                if e.high {
                    let length = python_hypot(ex - sx, ey - sy); // math.hypot
                    path.add(
                        e.match_ms as f64 + e.duration_ms as f64 / 2.0,
                        (sx + ex) / 2.0,
                        (sy + ey) / 2.0,
                        py_min(0.12 * length, 18.0),
                    );
                }
                path.add(end_ms as f64, ex, ey, 0.0);
            }
        }
        path
    }

    fn add(&mut self, ms: f64, px: f64, py: f64, pz: f64) {
        // A later event that happens before the previous flight finished interrupts it: drop the
        // planned remainder of that trajectory so the ball is where the event says it is.
        while self.t.last().is_some_and(|&last| last > ms) {
            self.t.pop();
            self.x.pop();
            self.y.pop();
            self.z.pop();
        }
        self.t.push(ms);
        self.x.push(px);
        self.y.push(py);
        self.z.push(pz);
    }

    /// `BallPath.at` for a whole array of times: three `np.interp` calls.
    pub fn at(&self, ms: &[f64]) -> Result<[Vec<f64>; 3], BuildError> {
        Ok([
            interp(ms, &self.t, &self.x)?,
            interp(ms, &self.t, &self.y)?,
            interp(ms, &self.t, &self.z)?,
        ])
    }
}

/// `np.interp(x, xp, fp)` for sorted (possibly repeated) `xp`, reproducing NumPy's compiled loop:
/// clamped ends, the value at the last of repeated sample points, `slope * (x - xp[j]) + fp[j]` with
/// `slope = dy / dx` (checked bit for bit against NumPy 2.5 in tests/ball_path.rs), and NumPy's
/// fallbacks for a NaN result.
pub fn interp(x: &[f64], xp: &[f64], fp: &[f64]) -> Result<Vec<f64>, BuildError> {
    let n = xp.len();
    if n == 0 {
        return Err(BuildError::Value("array of sample points is empty".into()));
    }
    let (left, right) = (fp[0], fp[n - 1]);
    if n == 1 {
        return Ok(x
            .iter()
            .map(|&v| if v.is_nan() { v } else { left })
            .collect());
    }
    let mut out = Vec::with_capacity(x.len());
    for &v in x {
        if v.is_nan() {
            out.push(v);
            continue;
        }
        if v > xp[n - 1] {
            out.push(right);
            continue;
        }
        if v < xp[0] {
            out.push(left);
            continue;
        }
        // NumPy's search returns j with xp[j] <= v < xp[j + 1]: the last of any repeated points.
        let j = xp.partition_point(|&p| p <= v) - 1;
        if j == n - 1 || xp[j] == v {
            out.push(fp[j]);
            continue;
        }
        let slope = (fp[j + 1] - fp[j]) / (xp[j + 1] - xp[j]);
        let mut r = slope * (v - xp[j]) + fp[j];
        if r.is_nan() {
            r = slope * (v - xp[j + 1]) + fp[j + 1];
            if r.is_nan() && fp[j] == fp[j + 1] {
                r = fp[j];
            }
        }
        out.push(r);
    }
    Ok(out)
}

/// Python's `math.hypot(x, y)`: CPython's `vector_norm` (Modules/mathmodule.c, 3.10+), which is more
/// accurate than C's `hypot` and so can differ from it in the last bit. Products and sums are split
/// into exact high and low parts (Dekker), as CPython does, so the result is the same bit for bit.
pub fn python_hypot(x: f64, y: f64) -> f64 {
    let (x, y) = (x.abs(), y.abs());
    if x.is_infinite() || y.is_infinite() {
        return f64::INFINITY;
    }
    let max = if y > x { y } else { x };
    if x.is_nan() || y.is_nan() {
        return f64::NAN;
    }
    if max == 0.0 {
        return max;
    }
    vector_norm([x, y], max)
}

fn vector_norm(mut vec: [f64; 2], max: f64) -> f64 {
    let max_e = frexp_exponent(max);
    if max_e < -1023 {
        // ldexp(1.0, -max_e) would overflow: rescale subnormals first.
        for v in &mut vec {
            *v /= f64::MIN_POSITIVE;
        }
        return f64::MIN_POSITIVE * vector_norm(vec, max / f64::MIN_POSITIVE);
    }
    let scale = ldexp_one(-max_e);
    let (mut csum, mut frac1, mut frac2) = (1.0f64, 0.0f64, 0.0f64);
    for v in vec {
        let x = v * scale; // lossless scaling
        let (hi, lo) = dl_mul(x, x); // lossless squaring
        let (s_hi, s_lo) = dl_fast_sum(csum, hi); // lossless addition
        csum = s_hi;
        frac1 += lo;
        frac2 += s_lo;
    }
    let mut h = (csum - 1.0 + (frac1 + frac2)).sqrt();
    let (hi, lo) = dl_mul(-h, h);
    let (s_hi, s_lo) = dl_fast_sum(csum, hi);
    csum = s_hi;
    frac1 += lo;
    frac2 += s_lo;
    let x = csum - 1.0 + (frac1 + frac2);
    h += x / (2.0 * h); // differential correction
    h / scale
}

/// The exponent `frexp` returns: `v = m * 2**e` with `0.5 <= m < 1` (for finite `v > 0`).
fn frexp_exponent(v: f64) -> i32 {
    let biased = ((v.to_bits() >> 52) & 0x7ff) as i32;
    if biased == 0 {
        // Subnormal: normalise by 2**54 first.
        return frexp_exponent(v * f64::from_bits((1023u64 + 54) << 52)) - 54;
    }
    biased - 1022
}

/// `ldexp(1.0, k)` for -1074 <= k <= 1023.
fn ldexp_one(k: i32) -> f64 {
    if k >= -1022 {
        f64::from_bits(((k + 1023) as u64) << 52)
    } else {
        f64::from_bits(1u64 << (k + 1074))
    }
}

/// Exact product `x * y = hi + lo` (Dekker's TwoProduct with Veltkamp splitting).
fn dl_mul(x: f64, y: f64) -> (f64, f64) {
    fn split(v: f64) -> (f64, f64) {
        let t = v * 134_217_729.0; // 2**27 + 1
        let hi = t - (t - v);
        (hi, v - hi)
    }
    let z = x * y;
    let (xh, xl) = split(x);
    let (yh, yl) = split(y);
    let zz = (((xh * yh - z) + xh * yl) + xl * yh) + xl * yl;
    (z, zz)
}

/// Exact sum `a + b = hi + lo` for `|a| >= |b|` (Dekker's Fast2Sum).
fn dl_fast_sum(a: f64, b: f64) -> (f64, f64) {
    let x = a + b;
    (x, (a - x) + b)
}

/// Python's `bisect.bisect_right`.
fn bisect_right(a: &[i64], x: i64) -> usize {
    let (mut lo, mut hi) = (0usize, a.len());
    while lo < hi {
        let mid = (lo + hi) / 2;
        if x < a[mid] {
            hi = mid;
        } else {
            lo = mid + 1;
        }
    }
    lo
}

/// The built tracking: `ms` (T), `period` (T), `ball` (T x 3, row-major), `xy` (T x N x 2, row-major,
/// NaN while a player is off the pitch).
#[derive(Debug)]
pub struct Built {
    pub ms: Vec<i64>,
    pub period: Vec<i8>,
    pub ball: Vec<f32>,
    pub xy: Vec<f32>,
}

fn side_of(sides: &mut [Side], team: i64) -> Result<&mut Side, BuildError> {
    usize::try_from(team)
        .ok()
        .and_then(|i| sides.get_mut(i))
        .ok_or_else(|| BuildError::Key(format!("team {team}")))
}

fn slot_of(side: &Side, player: i64) -> Result<usize, BuildError> {
    side.players
        .iter()
        .position(|&p| player >= 0 && p as i64 == player)
        .ok_or_else(|| BuildError::StopIteration(format!("player {player} is not on the pitch")))
}

/// `generate_tracking_py`, minus the packing: same ticks, same event handling, same movement model.
pub fn build(
    events: &Events,
    mut sides: Vec<Side>,
    top_speed_kmh: &[f64],
) -> Result<Built, BuildError> {
    let n_events = events.len();
    let n_players = top_speed_kmh.len();
    let ball = BallPath::new((0..n_events).map(|i| BallEvent {
        match_ms: events.match_ms[i],
        start: Events::point(events.start[i]),
        end: Events::point(events.end[i]),
        duration_ms: events.duration_ms[i],
        high: events.flags[i] & HIGH != 0,
    }));

    // Ticks: every 200 ms of every period, from its PERIOD_START to its PERIOD_END inclusive.
    let mut ticks: Vec<(i64, i64)> = Vec::new();
    let mut start_ms = 0i64;
    for i in 0..n_events {
        match events.kind[i] {
            PERIOD_START => start_ms = events.match_ms[i],
            PERIOD_END => {
                let mut ms = start_ms;
                while ms <= events.match_ms[i] {
                    ticks.push((events.period[i], ms));
                    ms += TICK_MS;
                }
            }
            _ => {}
        }
    }
    let n_ticks = ticks.len();
    let ms_arr: Vec<i64> = ticks.iter().map(|&(_, ms)| ms).collect();
    let ms_f: Vec<f64> = ms_arr.iter().map(|&ms| ms as f64).collect();
    let [bx, by, bz] = ball.at(&ms_f)?;
    let mut xy = vec![f32::NAN; n_ticks * n_players * 2];

    for side in &sides {
        if side.players.len() != side.anchors.len() || side.players.len() != side.units.len() {
            return Err(BuildError::Value(
                "a side's players, anchors and slots differ in length".into(),
            ));
        }
        if side.players.iter().any(|&p| p >= n_players) {
            return Err(BuildError::Value(
                "a side's player column is out of range".into(),
            ));
        }
    }
    let column = |p: i64| usize::try_from(p).ok().filter(|&c| c < n_players);

    let event_ms = events.match_ms;
    let mut pos: Vec<Option<XY>> = vec![None; n_players];
    let mut possessor: i64 = 0; // the home side
    let mut carrier: i64 = NONE;
    let mut pass_target: Option<(i64, XY, i64)> = None; // recipient, destination (abs), arrival ms
    let mut pressers: Vec<i64> = vec![-1; n_players]; // ms until which each player closes down the ball
    let mut ei = 0usize;
    let dt = TICK_MS as f64 / 1000.0;
    let lag = py_min(1.0, dt / SHAPE_LAG_S);
    let mut prev_period = 0i64;
    let mut shape_ref: XY = (52.5, 34.0);
    let mut targets: Vec<XY> = Vec::with_capacity(11);

    for (ti, &(period, ms)) in ticks.iter().enumerate() {
        // Apply every event up to this tick.
        let upto = bisect_right(event_ms, ms);
        while ei < upto {
            let i = ei;
            ei += 1;
            let (kind, flags) = (events.kind[i], events.flags[i]);
            let (team, player) = (events.team[i], events.player[i]);
            if kind == POSSESSION_CHANGE && team != NONE {
                possessor = team;
            }
            if kind == SUBSTITUTION && team != NONE && player != NONE && events.related[i] != NONE {
                let side = side_of(&mut sides, team)?;
                let k = slot_of(side, player)?;
                let related = events.related[i];
                let on =
                    column(related).ok_or_else(|| BuildError::Key(format!("player {related}")))?;
                side.players[k] = on;
                let off = column(player).expect("an on-pitch player has a column");
                pos[on] = Some(pos[off].take().unwrap_or((52.5, 1.0)));
            }
            if kind == CARD && flags & SENT_OFF != 0 && team != NONE {
                let side = side_of(&mut sides, team)?;
                let k = slot_of(side, player)?;
                let off = side.players.remove(k);
                side.anchors.remove(k);
                side.units.remove(k);
                pos[off] = None;
            }
            if kind == PRESSURE && player != NONE {
                if let Some(c) = column(player) {
                    pressers[c] = events.match_ms[i] + 1500;
                }
            }
            if flags & ON_BALL != 0 && player != NONE {
                carrier = player;
                pass_target = None;
                let end = Events::point(events.end[i]);
                if let (true, Some(dest)) =
                    (flags & TRAVEL != 0 && events.recipient[i] != NONE, end)
                {
                    if events.duration_ms[i] != 0 {
                        // While the ball travels nobody carries it; the intended recipient runs onto it.
                        carrier = NONE;
                        pass_target = Some((
                            events.recipient[i],
                            dest,
                            events.match_ms[i] + events.duration_ms[i],
                        ));
                    }
                }
            }
            if kind == SHOT {
                carrier = NONE;
            }
        }
        if let Some((recipient, _, arrival)) = pass_target {
            if ms >= arrival {
                carrier = recipient;
                pass_target = None;
            }
        }

        let ball_abs: XY = (bx[ti], by[ti]);
        let new_period = period != prev_period;
        prev_period = period;
        if new_period {
            shape_ref = ball_abs;
        } else {
            shape_ref = (
                shape_ref.0 + (ball_abs.0 - shape_ref.0) * lag,
                shape_ref.1 + (ball_abs.1 - shape_ref.1) * lag,
            );
        }
        for (si, side) in sides.iter().enumerate() {
            let attacks_pos = side.is_home == (period == 1);
            let to_frame = |p: XY| if attacks_pos { p } else { mirror(p) };
            let in_possession = si as i64 == possessor;
            team_shape(
                &side.anchors,
                &side.units,
                to_frame(shape_ref),
                in_possession,
                side.press,
                side.width,
                &mut targets,
            );
            for (&pid, &target_tf) in side.players.iter().zip(&targets) {
                let mut target = to_frame(target_tf);
                let mut urgent = false;
                if pid as i64 == carrier {
                    (target, urgent) = (ball_abs, true);
                } else if let Some((_, dest, _)) = pass_target.filter(|&(r, _, _)| pid as i64 == r)
                {
                    (target, urgent) = (dest, true);
                } else if pressers[pid] >= ms {
                    (target, urgent) = ((ball_abs.0 + 0.8, ball_abs.1 + 0.0), true);
                }
                let next = match pos[pid] {
                    Some(current) if !new_period => {
                        let delta = (target.0 - current.0, target.1 - current.1);
                        let dist = delta.0.hypot(delta.1); // np.hypot: the C library hypot
                        let top = top_speed_kmh[pid] / 3.6;
                        let speed = if urgent {
                            top * 0.97
                        } else {
                            py_min(
                                dist / REPOSITION_S,
                                top * if dist > 15.0 { 0.75 } else { 0.6 },
                            )
                        };
                        let step = py_min(dist, speed * dt);
                        if dist > 1e-6 {
                            (
                                current.0 + delta.0 / dist * step,
                                current.1 + delta.1 / dist * step,
                            )
                        } else {
                            current
                        }
                    }
                    _ => target,
                };
                pos[pid] = Some(next);
                let at = (ti * n_players + pid) * 2;
                xy[at] = next.0 as f32;
                xy[at + 1] = next.1 as f32;
            }
        }
    }

    let mut ball_out = Vec::with_capacity(n_ticks * 3);
    for ti in 0..n_ticks {
        ball_out.extend_from_slice(&[bx[ti] as f32, by[ti] as f32, bz[ti] as f32]);
    }
    Ok(Built {
        ms: ms_arr,
        period: ticks.iter().map(|&(p, _)| p as i8).collect(),
        ball: ball_out,
        xy,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn interp_clamps_and_takes_the_last_repeated_point() {
        let xp = [0.0, 10.0, 10.0, 20.0];
        let fp = [0.0, 1.0, 5.0, 7.0];
        let x = [-5.0, 0.0, 5.0, 10.0, 15.0, 20.0, 25.0];
        let got = interp(&x, &xp, &fp).unwrap();
        assert_eq!(got, vec![0.0, 0.0, 0.5, 5.0, 6.0, 7.0, 7.0]);
        assert_eq!(interp(&[1.0, 2.0], &[3.0], &[4.0]).unwrap(), vec![4.0, 4.0]);
        assert!(interp(&[1.0], &[], &[]).is_err());
    }

    #[test]
    fn bisect_right_matches_python() {
        let a = [0, 200, 200, 400];
        assert_eq!(bisect_right(&a, -1), 0);
        assert_eq!(bisect_right(&a, 0), 1);
        assert_eq!(bisect_right(&a, 200), 3);
        assert_eq!(bisect_right(&a, 500), 4);
    }

    #[test]
    fn a_later_event_interrupts_a_flight() {
        let pass = BallEvent {
            match_ms: 0,
            start: Some((10.0, 10.0)),
            end: Some((50.0, 10.0)),
            duration_ms: 2000,
            high: true,
        };
        let header = BallEvent {
            match_ms: 1500,
            start: Some((30.0, 20.0)),
            end: None,
            duration_ms: 0,
            high: false,
        };
        let path = BallPath::new([pass, header]);
        // The midpoint at 1000 ms stays; the planned arrival at 2000 ms is dropped.
        assert_eq!(path.t, vec![0.0, 1000.0, 1500.0]);
        assert_eq!(path.z[1], 0.12 * 40.0);
    }
}
