//! Physical metrics from tracking: a port of `pitchlens/interpret/physical.py`.
//!
//! Distance, high-speed running, sprints, top speed and ball speed, with the same 1 s sliding-window
//! speeds, the same per-period resets and the same threshold alerts, in the same order. The Python class
//! stays the reference; `engine/tests/unit/test_core_parity_physical.py` compares the two.
//!
//! Results match Python bit for bit, not just within tolerance: the arithmetic follows the Python order
//! of operations, `py_hypot` reproduces CPython's `math.hypot`, and `py_round2` reproduces
//! `round(x, 2)`, which `Tracking.frame` applies to every coordinate the live path sees.

use std::collections::{HashMap, VecDeque};

use ndarray::{ArrayView1, ArrayView2, ArrayView3};

pub const HSR_KMH: f64 = 19.8;
pub const SPRINT_KMH: f64 = 25.2;
pub const SPRINT_MIN_MS: i64 = 1000;
pub const SPEED_WINDOW_MS: i64 = 1000;
pub const TOP_SPEED_THRESHOLDS_KMH: [f64; 1] = [33.0];
pub const DISTANCE_THRESHOLDS_KM: [f64; 4] = [5.0, 8.0, 10.0, 12.0];
pub const SPRINT_COUNT_THRESHOLDS: [i64; 3] = [10, 20, 30];

// ------------------------------------------------------------------------------------------ CPython maths

/// `a * b` as an unevaluated sum `hi + lo`, exactly (Dekker's two-product; no FMA needed).
fn two_prod(a: f64, b: f64) -> (f64, f64) {
    fn split(v: f64) -> (f64, f64) {
        let c = 134_217_729.0 * v; // 2^27 + 1
        let hi = c - (c - v);
        (hi, v - hi)
    }
    let p = a * b;
    let (ah, al) = split(a);
    let (bh, bl) = split(b);
    (p, ((ah * bh - p) + ah * bl + al * bh) + al * bl)
}

/// Compensated sum of two doubles with `|a| >= |b|`.
fn fast_two_sum(a: f64, b: f64) -> (f64, f64) {
    let s = a + b;
    (s, (a - s) + b)
}

/// 2^e as a double, for e in the double range (normal or subnormal).
fn pow2(e: i32) -> f64 {
    if e >= -1022 {
        f64::from_bits(((e + 1023) as u64) << 52)
    } else {
        f64::from_bits(1u64 << (e + 1074))
    }
}

/// The exponent `frexp` returns: `v = m * 2^e` with `0.5 <= m < 1`, for finite non-zero `v`.
fn frexp_exp(v: f64) -> i32 {
    let bits = v.abs().to_bits();
    let biased = (bits >> 52) as i32;
    if biased == 0 {
        frexp_exp(v * pow2(54)) - 54 // subnormal
    } else {
        biased - 1022
    }
}

/// CPython 3.12's `math.hypot(x, y)` (`vector_norm` in mathmodule.c): a scaled, compensated sum of
/// squares with a final differential correction. libm's `hypot` can differ from it in the last ulp.
pub fn py_hypot(x: f64, y: f64) -> f64 {
    let (x, y) = (x.abs(), y.abs());
    let mut max = 0.0f64; // as CPython: NaN never becomes the max, so (NaN, inf) is inf
    for v in [x, y] {
        if v > max {
            max = v;
        }
    }
    if max.is_infinite() {
        return max;
    }
    if x.is_nan() || y.is_nan() {
        return f64::NAN;
    }
    if max == 0.0 {
        return max;
    }
    norm2(x, y, max)
}

fn norm2(x: f64, y: f64, max: f64) -> f64 {
    let max_e = frexp_exp(max);
    if max_e < -1023 {
        let tiny = f64::MIN_POSITIVE;
        return tiny * norm2(x / tiny, y / tiny, max / tiny);
    }
    let scale = pow2(-max_e);
    let (mut csum, mut frac1, mut frac2) = (1.0f64, 0.0f64, 0.0f64);
    for v in [x, y] {
        let v = v * scale;
        let (hi, lo) = two_prod(v, v);
        let (s, e) = fast_two_sum(csum, hi);
        csum = s;
        frac1 += lo;
        frac2 += e;
    }
    let mut h = (csum - 1.0 + (frac1 + frac2)).sqrt();
    let (hi, lo) = two_prod(-h, h);
    let (s, e) = fast_two_sum(csum, hi);
    csum = s;
    frac1 += lo;
    frac2 += e;
    let r = csum - 1.0 + (frac1 + frac2);
    h += r / (2.0 * h);
    h / scale
}

/// Python's `round(x, 2)`: the double nearest to x rounded to two decimals, ties to even on the exact
/// value of x (so `round(0.125, 2) == 0.12` and `round(2.675, 2) == 2.67`).
pub fn py_round2(x: f64) -> f64 {
    if !x.is_finite() {
        return x;
    }
    let (y, err) = two_prod(x, 100.0); // x * 100 == y + err exactly
    let f = y.floor();
    let d = (y - f - 0.5) + err; // sign of (x * 100 - (f + 0.5)), exactly
    let r = if d > 0.0 || (d == 0.0 && f % 2.0 != 0.0) {
        f + 1.0
    } else {
        f
    };
    let out = r / 100.0;
    if out == 0.0 {
        0.0f64.copysign(x)
    } else {
        out
    }
}

// ------------------------------------------------------------------------------------------ the tracker

/// Samples over the last ~1 s, for speeds that filter frame-to-frame jitter.
#[derive(Default, Clone, Debug)]
struct SpeedWindow(VecDeque<(i64, f64, f64)>);

impl SpeedWindow {
    /// km/h over the last ~1 s of samples (oldest sample in the window to now).
    fn speed(&mut self, t: i64, x: f64, y: f64) -> f64 {
        self.0.push_back((t, x, y));
        while let Some(&(t0, _, _)) = self.0.front() {
            if t - t0 > SPEED_WINDOW_MS {
                self.0.pop_front();
            } else {
                break;
            }
        }
        let (t0, x0, y0) = self.0[0];
        if t == t0 {
            return 0.0;
        }
        py_hypot(x - x0, y - y0) / ((t - t0) as f64 / 1000.0) * 3.6
    }
}

#[derive(Clone, Debug)]
pub struct Player {
    pub player_id: String,
    pub distance_m: f64,
    pub hsr_m: f64,
    pub sprint_m: f64,
    pub sprints: i64,
    pub top_speed_kmh: f64,
    pub speed_kmh: f64,
    window: SpeedWindow,
    last: Option<(i64, f64, f64)>,
    above_sprint_since: Option<i64>,
    sprint_counted: bool,
    fired: u8, // one bit per (kind, threshold), in `Kind::ALL` x thresholds order
}

impl Player {
    fn new(player_id: &str) -> Self {
        Self {
            player_id: player_id.to_owned(),
            distance_m: 0.0,
            hsr_m: 0.0,
            sprint_m: 0.0,
            sprints: 0,
            top_speed_kmh: 0.0,
            speed_kmh: 0.0,
            window: SpeedWindow::default(),
            last: None,
            above_sprint_since: None,
            sprint_counted: false,
            fired: 0,
        }
    }

    fn reset_period(&mut self) {
        self.window.0.clear();
        self.last = None;
        self.above_sprint_since = None;
        self.sprint_counted = false;
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    TopSpeed,
    Distance,
    SprintCount,
}

impl Kind {
    pub fn name(self) -> &'static str {
        match self {
            Kind::TopSpeed => "top_speed",
            Kind::Distance => "distance",
            Kind::SprintCount => "sprint_count",
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Alert {
    pub kind: Kind,
    pub player: usize,
    pub match_ms: i64,
    pub value: f64, // unrounded: Python rounds it to 2 decimals when it builds the PhysicalAlert
    pub threshold: f64,
}

#[derive(Default, Debug)]
pub struct Tracker {
    pub players: Vec<Player>, // in first-seen order, like the Python dict
    index: HashMap<String, usize>,
    pub ball_speed_kmh: f64,
    pub max_ball_speed_kmh: f64,
    ball_window: SpeedWindow,
    period_of_last_frame: Option<i64>,
}

impl Tracker {
    pub fn player_index(&mut self, player_id: &str) -> usize {
        if let Some(&i) = self.index.get(player_id) {
            return i;
        }
        let i = self.players.len();
        self.players.push(Player::new(player_id));
        self.index.insert(player_id.to_owned(), i);
        i
    }

    /// The per-frame preamble: a new period resets every window, then the ball speed.
    pub fn begin_frame(&mut self, period: i64, t: i64, ball_x: f64, ball_y: f64) {
        if self.period_of_last_frame != Some(period) {
            for p in &mut self.players {
                p.reset_period();
            }
            self.ball_window.0.clear();
            self.period_of_last_frame = Some(period);
        }
        self.ball_speed_kmh = self.ball_window.speed(t, ball_x, ball_y);
        if self.ball_speed_kmh > self.max_ball_speed_kmh {
            self.max_ball_speed_kmh = self.ball_speed_kmh;
        }
    }

    /// One player in the current frame, in frame order.
    pub fn step(&mut self, i: usize, t: i64, x: f64, y: f64, alerts: &mut Vec<Alert>) {
        let p = &mut self.players[i];
        if let Some((_, lx, ly)) = p.last {
            let step = py_hypot(x - lx, y - ly);
            p.distance_m += step;
            if p.speed_kmh >= HSR_KMH {
                p.hsr_m += step;
            }
            if p.speed_kmh >= SPRINT_KMH {
                p.sprint_m += step;
            }
        }
        p.last = Some((t, x, y));
        p.speed_kmh = p.window.speed(t, x, y);
        if p.speed_kmh > p.top_speed_kmh {
            p.top_speed_kmh = p.speed_kmh;
        }
        if p.speed_kmh >= SPRINT_KMH {
            let since = *p.above_sprint_since.get_or_insert(t);
            if !p.sprint_counted && t - since >= SPRINT_MIN_MS {
                p.sprints += 1;
                p.sprint_counted = true;
            }
        } else {
            p.above_sprint_since = None;
            p.sprint_counted = false;
        }
        thresholds(p, i, t, alerts);
    }
}

fn thresholds(p: &mut Player, i: usize, t: i64, alerts: &mut Vec<Alert>) {
    let sprint_thresholds = SPRINT_COUNT_THRESHOLDS.map(|v| v as f64);
    let checks: [(Kind, f64, &[f64]); 3] = [
        (Kind::TopSpeed, p.speed_kmh, &TOP_SPEED_THRESHOLDS_KMH),
        (
            Kind::Distance,
            p.distance_m / 1000.0,
            &DISTANCE_THRESHOLDS_KM,
        ),
        (Kind::SprintCount, p.sprints as f64, &sprint_thresholds),
    ];
    let mut bit = 0;
    for (kind, value, limits) in checks {
        for &threshold in limits {
            if value >= threshold && p.fired & (1 << bit) == 0 {
                p.fired |= 1 << bit;
                alerts.push(Alert {
                    kind,
                    player: i,
                    match_ms: t,
                    value,
                    threshold,
                });
            }
            bit += 1;
        }
    }
}

/// Frames `start..end` of a match's tracking arrays, exactly as `Tracking.frame(i)` presents them (players
/// in column order, off-pitch NaN columns skipped, coordinates rounded to 2 decimals). A player is added
/// when first seen on the pitch, so `players` keeps the order frame-by-frame `apply` would give it.
pub fn apply_range(
    tracker: &mut Tracker,
    ms: ArrayView1<i64>,
    period: Period,
    ball: ArrayView2<f32>,
    xy: ArrayView3<f32>,
    player_ids: &[String],
    (start, end): (usize, usize),
) -> Vec<Alert> {
    let mut cols: Vec<Option<usize>> = player_ids
        .iter()
        .map(|id| tracker.index.get(id).copied())
        .collect();
    let mut alerts = Vec::new();
    for f in start..end {
        let t = ms[f];
        let period = match &period {
            Period::Fixed(p) => *p,
            Period::PerFrame(arr) => arr[f] as i64,
        };
        let bx = py_round2(ball[[f, 0]] as f64);
        let by = py_round2(ball[[f, 1]] as f64);
        tracker.begin_frame(period, t, bx, by);
        for (c, col) in cols.iter_mut().enumerate() {
            let x = xy[[f, c, 0]] as f64;
            if x.is_nan() {
                continue;
            }
            let i = *col.get_or_insert_with(|| tracker.player_index(&player_ids[c]));
            tracker.step(
                i,
                t,
                py_round2(x),
                py_round2(xy[[f, c, 1]] as f64),
                &mut alerts,
            );
        }
    }
    alerts
}

pub enum Period<'a> {
    Fixed(i64),
    PerFrame(ArrayView1<'a, i8>),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round2_matches_cpython() {
        // (x, round(x, 2)) captured from CPython 3.12, as bit patterns.
        let cases: [(f64, u64); 8] = [
            (0.125, 0x3fbeb851eb851eb8),
            (0.375, 0x3fd851eb851eb852),
            (34.125, 0x40410f5c28f5c28f),
            (2.675, 0x40055c28f5c28f5c),
            (-0.001, 0x8000000000000000),
            (1.005, 0x3ff0000000000000),
            (104.99999, 0x405a400000000000),
            (-3.335, 0xc00aa3d70a3d70a4),
        ];
        for (x, want) in cases {
            assert_eq!(py_round2(x).to_bits(), want, "round({x}, 2)");
        }
    }

    #[test]
    fn hypot_matches_cpython() {
        // (x, y, math.hypot(x, y)) captured from CPython 3.12, as bit patterns.
        let cases: [(f64, f64, u64); 5] = [
            (0.1, 0.2, 0x3fcc9f25c5bfedda),
            (0.001, 7.77, 0x401f147ae59934a7),
            (52.33, -14.01, 0x404b16235f8f48a1),
            (0.01, 0.01, 0x3f8cf68d4fff04dd),
            (1e300, 1e300, 0x7e40e4d50f99b211),
        ];
        for (x, y, want) in cases {
            assert_eq!(py_hypot(x, y).to_bits(), want, "hypot({x}, {y})");
        }
        assert_eq!(py_hypot(3.0, 4.0), 5.0);
        assert_eq!(py_hypot(0.0, -0.0), 0.0);
        assert_eq!(py_hypot(5e-324, 0.0), 5e-324);
        assert!(py_hypot(f64::NAN, 1.0).is_nan());
        assert_eq!(py_hypot(f64::NAN, f64::INFINITY), f64::INFINITY);
    }

    #[test]
    fn window_speed_spans_one_second() {
        let mut w = SpeedWindow::default();
        assert_eq!(w.speed(0, 0.0, 0.0), 0.0);
        for k in 1..=10 {
            w.speed(k * 200, k as f64 * 2.0, 0.0); // 10 m/s
        }
        // The oldest sample kept is exactly 1 s old: 10 m in 1 s is 36 km/h.
        assert!((w.speed(2200, 22.0, 0.0) - 36.0).abs() < 1e-9);
        assert_eq!(w.0.len(), 6);
    }

    fn run(tracker: &mut Tracker, frames: &[(i64, i64, f64)]) -> Vec<Alert> {
        let mut alerts = Vec::new();
        for &(period, t, x) in frames {
            tracker.begin_frame(period, t, 0.0, 0.0);
            let i = tracker.player_index("p");
            tracker.step(i, t, x, 0.0, &mut alerts);
        }
        alerts
    }

    #[test]
    fn sprints_need_a_second_above_threshold_and_alert_once() {
        let mut tracker = Tracker::default();
        // 9.5 m/s (34.2 km/h) for 3 s: one sprint, one top-speed alert.
        let frames: Vec<_> = (0..=15).map(|k| (1, k * 200, k as f64 * 1.9)).collect();
        let alerts = run(&mut tracker, &frames);
        let p = &tracker.players[0];
        assert_eq!(p.sprints, 1);
        assert!(p.top_speed_kmh > 34.0 && p.hsr_m > 0.0 && p.sprint_m > 0.0);
        assert_eq!(alerts.len(), 1);
        assert_eq!(
            (alerts[0].kind, alerts[0].threshold),
            (Kind::TopSpeed, 33.0)
        );
        assert!((p.distance_m - 28.5).abs() < 1e-9);
    }

    #[test]
    fn distance_never_spans_half_time() {
        let mut tracker = Tracker::default();
        run(
            &mut tracker,
            &[(1, 0, 0.0), (1, 200, 1.0), (2, 400, 50.0), (2, 600, 51.0)],
        );
        assert!((tracker.players[0].distance_m - 2.0).abs() < 1e-12);
    }
}
