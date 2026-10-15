//! Futures: a Monte Carlo match engine that plays out the rest of a match many times.
//!
//! The closed-form win probability (`win_prob.rs`) assumes each side scores at one constant rate until
//! full time. Futures simulates the remaining minutes instead, so the rates can depend on what happens
//! along the way (docs/rust-expansion-plan.md §3):
//!
//! - **Game state.** A trailing side raises its scoring rate and a leading side sits deeper. The effect
//!   grows with the score difference (one goal, or two or more) and with how late it is.
//! - **Momentum.** The current momentum and field tilt lift the side on top now; the lift halves every
//!   few minutes.
//! - **Discipline.** Further red cards arrive at each side's card and foul rate, and every dismissal
//!   applies the closed form's x0.7 (own) / x1.2 (opponent) multipliers from that minute on.
//!
//! Every effect has a named constant and a switch in [`Effects`]; with all three off the model is the
//! closed form's (constant Poisson rates), which the calibration tests check.
//!
//! **Simulation.** Time runs in 0.25-minute steps to full time, including expected added time. Rates
//! are constant within a step and depend on the step and the path's state (score difference, red cards).
//! Rather than drawing a Poisson count per side per step (about 760 draws per path), each path draws the
//! time to its next event from the cumulative hazard, which is the same process: an exponential draw,
//! a binary search in a table of cumulative hazards for the path's state, then one uniform draw to pick
//! the event (home goal, away goal, home red, away red). The state changes and the walk continues from
//! that instant. A path costs a few draws per event, so 100,000 paths take milliseconds.
//!
//! **Reproducible.** Paths run in fixed chunks of [`CHUNK_PATHS`], each with its own
//! `Xoshiro256PlusPlus` seeded from the run's seed and the chunk index. Chunks are tallied in order, so
//! the result is identical with or without the `parallel` feature (rayon) and on any number of threads.

use rand_xoshiro::rand_core::{RngCore, SeedableRng};
use rand_xoshiro::Xoshiro256PlusPlus;

use crate::win_prob::{py_pow_int, RED_CARD_OPPONENT, RED_CARD_OWN};

/// Length of one simulation step, in minutes.
pub const STEP_MINUTES: f64 = 0.25;
/// Paths per run unless the caller asks for another number.
pub const DEFAULT_PATHS: u32 = 100_000;
/// Paths per chunk: the unit of parallel work and of seeding.
pub const CHUNK_PATHS: u32 = 2048;
/// Upper bound on paths per run (memory for next-goal times stays under 80 MB).
pub const MAX_PATHS: u32 = 10_000_000;

/// Game state: a side one goal down scores up to 30% faster by the end of the match...
pub const GAME_STATE_TRAILING_BOOST: f64 = 0.30;
/// ...and a side one goal up up to 15% slower.
pub const GAME_STATE_LEADING_DROP: f64 = 0.15;
/// Two goals or more apart, both effects are this much larger.
pub const GAME_STATE_TWO_GOAL_SCALE: f64 = 1.5;
/// The effect is zero with this many minutes left and grows linearly to full strength at full time.
pub const GAME_STATE_RAMP_MINUTES: f64 = 90.0;

/// Momentum: the side on top scores up to 35% faster (and the other side as much slower) right now...
pub const MOMENTUM_LIFT: f64 = 0.35;
/// ...from a signal mixing momentum (home minus away threat, -1..1)...
pub const MOMENTUM_WEIGHT: f64 = 0.7;
/// ...and field tilt (home share of final-third touches, as -1..1)...
pub const TILT_WEIGHT: f64 = 0.3;
/// ...and the lift halves every this many minutes.
pub const MOMENTUM_HALF_LIFE_MINUTES: f64 = 4.0;

/// Discipline: red cards per team per 90 minutes for a side booked and fouling at the average rate.
pub const RED_CARDS_PER_90: f64 = 0.08;
/// Average yellow plus red cards per team per 90 minutes.
pub const CARDS_PER_90: f64 = 2.0;
/// Average fouls per team per 90 minutes.
pub const FOULS_PER_90: f64 = 11.0;
/// The side's own card and foul rates are blended with this many minutes of average behaviour.
pub const DISCIPLINE_PRIOR_MINUTES: f64 = 30.0;
/// The side's red-card rate stays within these multiples of the average.
pub const DISCIPLINE_MIN_INDEX: f64 = 0.25;
pub const DISCIPLINE_MAX_INDEX: f64 = 4.0;
/// No side is reduced below eight players in a simulated future.
pub const MAX_REDS: u32 = 3;

/// The most likely final scores reported, with everything else as "other".
pub const TOP_SCORES: usize = 10;
const SCORE_GRID: usize = 31; // final scores 0..=30 per side are tallied exactly; beyond is "other"
const GAME_STATES: usize = 5; // goal difference from home's view: <= -2, -1, 0, 1, >= 2

/// Which effects a run models. All on is the futures model; all off is the closed form's.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Effects {
    pub game_state: bool,
    pub momentum: bool,
    pub discipline: bool,
}

impl Effects {
    pub const ALL: Effects = Effects {
        game_state: true,
        momentum: true,
        discipline: true,
    };
    pub const NONE: Effects = Effects {
        game_state: false,
        momentum: false,
        discipline: false,
    };
}

/// The match as it stands, and how to simulate it. Index 0 is home, 1 away.
#[derive(Clone, Debug, PartialEq)]
pub struct FuturesInput {
    /// Goals so far.
    pub score: [u32; 2],
    /// Clock minute the simulation starts from (the next-goal minute is reported on this clock).
    pub minute: f64,
    /// Minutes left including expected added time (`win_prob::remaining_minutes`).
    pub minutes_left: f64,
    /// Scoring rates per 90 minutes before red cards (`win_prob::scoring_rate_per_90`).
    pub rate_90: [f64; 2],
    /// Red cards so far.
    pub reds: [u32; 2],
    /// Momentum now: home minus away decayed threat, -1..1.
    pub momentum: f64,
    /// Field tilt now: home share of final-third touches, 0..1.
    pub field_tilt: f64,
    /// Red cards per 90 minutes from here ([`red_card_rate_90`]).
    pub red_rate_90: [f64; 2],
    pub effects: Effects,
    pub paths: u32,
    pub seed: u64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ScoreChance {
    pub home: u32,
    pub away: u32,
    pub p: f64,
}

/// For the side behind now: how often it avoids defeat, and how often it wins.
#[derive(Clone, Debug, PartialEq)]
pub struct Comeback {
    /// 0 home, 1 away.
    pub side: usize,
    pub avoid_defeat: f64,
    pub win: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct NextGoal {
    pub home: f64,
    pub away: f64,
    pub none: f64,
    /// Median clock minute of the next goal, over the paths that have one.
    pub median_minute: Option<f64>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Futures {
    pub paths: u32,
    pub home: f64,
    pub draw: f64,
    pub away: f64,
    /// The most likely final scores, most likely first (ties: fewer home goals, then fewer away goals).
    pub scores: Vec<ScoreChance>,
    /// Probability of every final score not in `scores`.
    pub other: f64,
    /// None when the score is level.
    pub comeback: Option<Comeback>,
    pub next_goal: NextGoal,
    /// Expected goals still to come, per side.
    pub goals_left: [f64; 2],
}

#[derive(Debug, Clone, PartialEq)]
pub enum FuturesError {
    Input(String),
}

impl std::fmt::Display for FuturesError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            FuturesError::Input(msg) => f.write_str(msg),
        }
    }
}

impl std::error::Error for FuturesError {}

/// A side's red cards per 90 minutes from here: the average rate scaled by how often the side has been
/// booked and has fouled so far, each blended with [`DISCIPLINE_PRIOR_MINUTES`] of average behaviour.
pub fn red_card_rate_90(cards: u32, fouls: u32, minutes_played: f64) -> f64 {
    let played = minutes_played.max(0.0);
    let span = (played + DISCIPLINE_PRIOR_MINUTES) / 90.0;
    let prior = DISCIPLINE_PRIOR_MINUTES / 90.0;
    let card_index = (cards as f64 + CARDS_PER_90 * prior) / (CARDS_PER_90 * span);
    let foul_index = (fouls as f64 + FOULS_PER_90 * prior) / (FOULS_PER_90 * span);
    let index =
        (0.5 * card_index + 0.5 * foul_index).clamp(DISCIPLINE_MIN_INDEX, DISCIPLINE_MAX_INDEX);
    RED_CARDS_PER_90 * index
}

fn splitmix64(mut z: u64) -> u64 {
    z = z.wrapping_add(0x9E37_79B9_7F4A_7C15);
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

/// The run's seed for a match and an event: FNV-1a of the match id mixed with the event's sequence
/// number, kept to 53 bits so it survives a trip through JSON and JavaScript numbers.
pub fn futures_seed(match_id: &str, seq: u64) -> u64 {
    let mut h: u64 = 0xCBF2_9CE4_8422_2325;
    for b in match_id.bytes() {
        h ^= b as u64;
        h = h.wrapping_mul(0x0000_0100_0000_01B3);
    }
    splitmix64(h ^ splitmix64(seq)) & ((1u64 << 53) - 1)
}

fn chunk_seed(seed: u64, chunk: u64) -> u64 {
    splitmix64(seed ^ splitmix64(chunk.wrapping_add(1)))
}

/// Uniform in (0, 1].
fn unit_open0(rng: &mut Xoshiro256PlusPlus) -> f64 {
    ((rng.next_u64() >> 11) + 1) as f64 * (1.0 / 9_007_199_254_740_992.0)
}

/// Uniform in [0, 1).
fn unit(rng: &mut Xoshiro256PlusPlus) -> f64 {
    (rng.next_u64() >> 11) as f64 * (1.0 / 9_007_199_254_740_992.0)
}

/// The rates a run uses, as tables over steps and path states.
struct Model {
    n: usize,
    start: Vec<f64>,    // minutes from now at the start of each step
    len: Vec<f64>,      // each step's length in minutes (the last one may be short)
    base: [f64; 2],     // goals per minute before every effect
    gs: [Vec<f64>; 2],  // game-state factor per side, [state * n + step]
    mom: [Vec<f64>; 2], // momentum factor per side, per step
    red_per_min: [f64; 2],
    reds0: [u32; 2],
    levels: [usize; 2],      // red-card levels a path can reach per side
    red_mult: Vec<[f64; 2]>, // red-card rate multipliers per side, [home level * levels[1] + away level]
    cum: Vec<f64>,           // cumulative hazard per path state, [state * (n + 1) + step]
}

impl Model {
    fn new(inp: &FuturesInput) -> Model {
        let left = inp.minutes_left.max(0.0);
        let n = if left > 0.0 {
            (left / STEP_MINUTES).ceil() as usize
        } else {
            0
        };
        let mut start = Vec::with_capacity(n);
        let mut len = Vec::with_capacity(n);
        for k in 0..n {
            let s = k as f64 * STEP_MINUTES;
            start.push(s);
            len.push((left - s).min(STEP_MINUTES));
        }
        let fx = inp.effects;
        let mut gs = [vec![1.0; GAME_STATES * n], vec![1.0; GAME_STATES * n]];
        if fx.game_state {
            for k in 0..n {
                let remaining = left - (start[k] + len[k] / 2.0);
                let urgency = (1.0 - remaining / GAME_STATE_RAMP_MINUTES).clamp(0.0, 1.0);
                for (c, d) in (-2i32..=2).enumerate() {
                    gs[0][c * n + k] = game_state_factor(d, urgency);
                    gs[1][c * n + k] = game_state_factor(-d, urgency);
                }
            }
        }
        let mut mom = [vec![1.0; n], vec![1.0; n]];
        if fx.momentum {
            let signal = (MOMENTUM_WEIGHT * inp.momentum
                + TILT_WEIGHT * (2.0 * inp.field_tilt - 1.0))
                .clamp(-1.0, 1.0);
            for k in 0..n {
                let mid = start[k] + len[k] / 2.0;
                let lift = MOMENTUM_LIFT * signal * 0.5f64.powf(mid / MOMENTUM_HALF_LIFE_MINUTES);
                mom[0][k] = 1.0 + lift;
                mom[1][k] = 1.0 - lift;
            }
        }
        let levels = [0usize, 1].map(|i| {
            if fx.discipline && inp.reds[i] < MAX_REDS {
                (MAX_REDS - inp.reds[i]) as usize + 1
            } else {
                1
            }
        });
        let mut model = Model {
            n,
            start,
            len,
            base: [inp.rate_90[0] / 90.0, inp.rate_90[1] / 90.0],
            gs,
            mom,
            red_per_min: if fx.discipline {
                [inp.red_rate_90[0] / 90.0, inp.red_rate_90[1] / 90.0]
            } else {
                [0.0, 0.0]
            },
            reds0: inp.reds,
            levels,
            red_mult: Vec::new(),
            cum: Vec::new(),
        };
        let mult =
            |own: u32, opp: u32| py_pow_int(RED_CARD_OWN, own) * py_pow_int(RED_CARD_OPPONENT, opp);
        for rh in 0..levels[0] as u32 {
            for ra in 0..levels[1] as u32 {
                let (h, a) = (inp.reds[0] + rh, inp.reds[1] + ra);
                model.red_mult.push([mult(h, a), mult(a, h)]);
            }
        }
        let states = GAME_STATES * levels[0] * levels[1];
        let mut cum = vec![0.0; states * (n + 1)];
        for s in 0..states {
            let row = &mut cum[s * (n + 1)..(s + 1) * (n + 1)];
            for k in 0..n {
                let r = model.rates(s, k);
                row[k + 1] = row[k] + (r[0] + r[1] + r[2] + r[3]) * model.len[k];
            }
        }
        model.cum = cum;
        model
    }

    fn state(&self, diff: i64, reds: [u32; 2]) -> usize {
        let c = (diff.clamp(-2, 2) + 2) as usize;
        let rh = (reds[0] - self.reds0[0]) as usize;
        let ra = (reds[1] - self.reds0[1]) as usize;
        (c * self.levels[0] + rh) * self.levels[1] + ra
    }

    fn unpack(&self, s: usize) -> (usize, [u32; 2]) {
        let ra = s % self.levels[1];
        let rest = s / self.levels[1];
        let rh = rest % self.levels[0];
        let c = rest / self.levels[0];
        (c, [self.reds0[0] + rh as u32, self.reds0[1] + ra as u32])
    }

    /// Per-minute rates in state `s` during step `k`: home goal, away goal, home red, away red.
    fn rates(&self, s: usize, k: usize) -> [f64; 4] {
        let (c, reds) = self.unpack(s);
        let n = self.n;
        let mult = self.red_mult[s % (self.levels[0] * self.levels[1])];
        let home = self.base[0] * mult[0] * self.gs[0][c * n + k] * self.mom[0][k];
        let away = self.base[1] * mult[1] * self.gs[1][c * n + k] * self.mom[1][k];
        let red = |i: usize| {
            if (reds[i] - self.reds0[i]) as usize + 1 < self.levels[i] {
                self.red_per_min[i]
            } else {
                0.0
            }
        };
        [home, away, red(0), red(1)]
    }

    fn row(&self, s: usize) -> &[f64] {
        &self.cum[s * (self.n + 1)..(s + 1) * (self.n + 1)]
    }
}

/// A side's game-state factor at goal difference `d` (its own goals minus the opponent's).
fn game_state_factor(d: i32, urgency: f64) -> f64 {
    let scale = if d.abs() >= 2 {
        GAME_STATE_TWO_GOAL_SCALE
    } else {
        1.0
    };
    match d.signum() {
        -1 => 1.0 + GAME_STATE_TRAILING_BOOST * scale * urgency,
        1 => 1.0 - GAME_STATE_LEADING_DROP * scale * urgency,
        _ => 1.0,
    }
}

/// Counts from a chunk of paths; chunks merge in order.
#[derive(Clone)]
struct Tally {
    paths: u64,
    result: [u64; 3],     // home win, draw, away win
    grid: Vec<u64>,       // final scores, [home * SCORE_GRID + away]
    next: [u64; 3],       // home next, away next, no more goals
    first_goal: Vec<f64>, // minutes from now of each path's next goal
    goals: [u64; 2],
}

impl Tally {
    fn new() -> Tally {
        Tally {
            paths: 0,
            result: [0; 3],
            grid: vec![0; SCORE_GRID * SCORE_GRID],
            next: [0; 3],
            first_goal: Vec::new(),
            goals: [0; 2],
        }
    }

    fn merge(&mut self, o: &Tally) {
        self.paths += o.paths;
        for i in 0..3 {
            self.result[i] += o.result[i];
            self.next[i] += o.next[i];
        }
        for (a, b) in self.grid.iter_mut().zip(&o.grid) {
            *a += b;
        }
        self.first_goal.extend_from_slice(&o.first_goal);
        self.goals[0] += o.goals[0];
        self.goals[1] += o.goals[1];
    }
}

fn run_path(model: &Model, inp: &FuturesInput, rng: &mut Xoshiro256PlusPlus, tally: &mut Tally) {
    let n = model.n;
    let mut x = 0.0f64; // position in steps
    let mut goals = [0u32; 2];
    let mut reds = inp.reds;
    let mut diff = inp.score[0] as i64 - inp.score[1] as i64;
    let mut first: Option<(usize, f64)> = None;
    loop {
        let s = model.state(diff, reds);
        let row = model.row(s);
        let k0 = x.floor() as usize;
        let now = if k0 >= n {
            row[n]
        } else {
            row[k0] + (x - k0 as f64) * (row[k0 + 1] - row[k0])
        };
        let target = now - unit_open0(rng).ln();
        if target >= row[n] {
            break;
        }
        // row[k] <= target < row[k + 1]: the event falls in step k.
        let k = row.partition_point(|&h| h <= target) - 1;
        let frac = (target - row[k]) / (row[k + 1] - row[k]);
        x = k as f64 + frac;
        let r = model.rates(s, k);
        let u = unit(rng) * (r[0] + r[1] + r[2] + r[3]);
        if u < r[0] + r[1] {
            let side = if u < r[0] { 0 } else { 1 };
            goals[side] += 1;
            diff += if side == 0 { 1 } else { -1 };
            if first.is_none() {
                first = Some((side, model.start[k] + frac * model.len[k]));
            }
        } else if u < r[0] + r[1] + r[2] {
            reds[0] += 1;
        } else {
            reds[1] += 1;
        }
    }
    tally.paths += 1;
    let (h, a) = (inp.score[0] + goals[0], inp.score[1] + goals[1]);
    tally.result[if h > a {
        0
    } else if h == a {
        1
    } else {
        2
    }] += 1;
    if (h as usize) < SCORE_GRID && (a as usize) < SCORE_GRID {
        tally.grid[h as usize * SCORE_GRID + a as usize] += 1;
    }
    match first {
        Some((side, t)) => {
            tally.next[side] += 1;
            tally.first_goal.push(t);
        }
        None => tally.next[2] += 1,
    }
    tally.goals[0] += goals[0] as u64;
    tally.goals[1] += goals[1] as u64;
}

fn run_chunk(model: &Model, inp: &FuturesInput, chunk: u64) -> Tally {
    let first = chunk * CHUNK_PATHS as u64;
    let count = (inp.paths as u64 - first).min(CHUNK_PATHS as u64);
    let mut rng = Xoshiro256PlusPlus::seed_from_u64(chunk_seed(inp.seed, chunk));
    let mut tally = Tally::new();
    for _ in 0..count {
        run_path(model, inp, &mut rng, &mut tally);
    }
    tally
}

fn validate(inp: &FuturesInput) -> Result<(), FuturesError> {
    let bad = |what: &str| Err(FuturesError::Input(what.to_string()));
    if inp.paths == 0 || inp.paths > MAX_PATHS {
        return bad(&format!("paths must be between 1 and {MAX_PATHS}"));
    }
    let finite = [
        inp.minute,
        inp.minutes_left,
        inp.momentum,
        inp.field_tilt,
        inp.rate_90[0],
        inp.rate_90[1],
        inp.red_rate_90[0],
        inp.red_rate_90[1],
    ];
    if finite.iter().any(|v| !v.is_finite()) {
        return bad("every number in the input must be finite");
    }
    if inp.rate_90.iter().chain(&inp.red_rate_90).any(|&v| v < 0.0) {
        return bad("rates must not be negative");
    }
    if inp.minutes_left > 200.0 {
        return bad("minutes_left must be at most 200");
    }
    Ok(())
}

/// Simulate the rest of the match `inp.paths` times (in parallel with the `parallel` feature).
pub fn simulate(inp: &FuturesInput) -> Result<Futures, FuturesError> {
    simulate_with(inp, true)
}

/// [`simulate`] on one thread; the result is identical.
pub fn simulate_sequential(inp: &FuturesInput) -> Result<Futures, FuturesError> {
    simulate_with(inp, false)
}

fn simulate_with(inp: &FuturesInput, parallel: bool) -> Result<Futures, FuturesError> {
    validate(inp)?;
    let model = Model::new(inp);
    let chunks = (inp.paths as u64).div_ceil(CHUNK_PATHS as u64);
    let tallies: Vec<Tally> = run_chunks(&model, inp, chunks, parallel);
    let mut total = Tally::new();
    total.first_goal.reserve(inp.paths as usize);
    for t in &tallies {
        total.merge(t);
    }
    Ok(summarise(inp, total))
}

#[cfg(feature = "parallel")]
fn run_chunks(model: &Model, inp: &FuturesInput, chunks: u64, parallel: bool) -> Vec<Tally> {
    use rayon::prelude::*;
    if parallel && chunks > 1 {
        (0..chunks)
            .into_par_iter()
            .map(|c| run_chunk(model, inp, c))
            .collect()
    } else {
        (0..chunks).map(|c| run_chunk(model, inp, c)).collect()
    }
}

#[cfg(not(feature = "parallel"))]
fn run_chunks(model: &Model, inp: &FuturesInput, chunks: u64, _parallel: bool) -> Vec<Tally> {
    (0..chunks).map(|c| run_chunk(model, inp, c)).collect()
}

fn summarise(inp: &FuturesInput, mut t: Tally) -> Futures {
    let paths = t.paths as f64;
    let p = |c: u64| c as f64 / paths;
    let mut cells: Vec<(u64, u32, u32)> = t
        .grid
        .iter()
        .enumerate()
        .filter(|(_, &c)| c > 0)
        .map(|(i, &c)| (c, (i / SCORE_GRID) as u32, (i % SCORE_GRID) as u32))
        .collect();
    cells.sort_by(|a, b| b.0.cmp(&a.0).then(a.1.cmp(&b.1)).then(a.2.cmp(&b.2)));
    cells.truncate(TOP_SCORES);
    let top: u64 = cells.iter().map(|c| c.0).sum();
    let scores = cells
        .iter()
        .map(|&(c, home, away)| ScoreChance {
            home,
            away,
            p: p(c),
        })
        .collect();
    let (home, draw, away) = (p(t.result[0]), p(t.result[1]), p(t.result[2]));
    let comeback = match inp.score[0].cmp(&inp.score[1]) {
        std::cmp::Ordering::Less => Some(Comeback {
            side: 0,
            avoid_defeat: p(t.result[0] + t.result[1]),
            win: home,
        }),
        std::cmp::Ordering::Greater => Some(Comeback {
            side: 1,
            avoid_defeat: p(t.result[2] + t.result[1]),
            win: away,
        }),
        std::cmp::Ordering::Equal => None,
    };
    let median_minute = if t.first_goal.is_empty() {
        None
    } else {
        let mid = t.first_goal.len() / 2;
        let (_, m, _) = t.first_goal.select_nth_unstable_by(mid, f64::total_cmp);
        Some(inp.minute + *m)
    };
    Futures {
        paths: t.paths as u32,
        home,
        draw,
        away,
        scores,
        other: p(t.paths - top),
        comeback,
        next_goal: NextGoal {
            home: p(t.next[0]),
            away: p(t.next[1]),
            none: p(t.next[2]),
            median_minute,
        },
        goals_left: [t.goals[0] as f64 / paths, t.goals[1] as f64 / paths],
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::win_prob::{remaining_minutes, win_probability};

    fn input(score: [u32; 2], minute: f64, effects: Effects, paths: u32) -> FuturesInput {
        let period = if minute < 45.0 { 1 } else { 2 };
        FuturesInput {
            score,
            minute,
            minutes_left: remaining_minutes(minute, period, None),
            rate_90: [1.45, 1.15],
            reds: [0, 0],
            momentum: 0.0,
            field_tilt: 0.5,
            red_rate_90: [
                red_card_rate_90(1, 6, minute),
                red_card_rate_90(1, 6, minute),
            ],
            effects,
            paths,
            seed: futures_seed("TEST", 1),
        }
    }

    /// With every effect off the futures are the closed form's Poisson model: home, draw and away agree
    /// with `win_probability` within 4 standard errors at 50 match states.
    #[test]
    fn calibration_matches_closed_form_with_effects_off() {
        let paths = 40_000;
        let mut checked = 0;
        for (i, minute) in [0.0, 12.0, 30.0, 44.0, 46.0, 60.0, 71.0, 80.0, 88.0, 93.0]
            .iter()
            .enumerate()
        {
            for (j, (score, reds)) in [
                ([0, 0], [0, 0]),
                ([1, 0], [0, 0]),
                ([0, 2], [0, 1]),
                ([2, 2], [1, 0]),
                ([3, 1], [0, 0]),
            ]
            .iter()
            .enumerate()
            {
                let mut inp = input(*score, *minute, Effects::NONE, paths);
                inp.reds = *reds;
                inp.rate_90 = [0.9 + 0.1 * i as f64, 1.6 - 0.15 * j as f64];
                inp.seed = futures_seed("CAL", (i * 10 + j) as u64);
                let f = simulate_sequential(&inp).unwrap();
                let wp = win_probability(
                    score[0] as i64,
                    score[1] as i64,
                    inp.rate_90[0],
                    inp.rate_90[1],
                    inp.minutes_left,
                    reds[0],
                    reds[1],
                );
                for (got, want) in [(f.home, wp.home), (f.draw, wp.draw), (f.away, wp.away)] {
                    let sigma = (want * (1.0 - want) / paths as f64).sqrt().max(1e-9);
                    assert!(
                        (got - want).abs() <= 4.0 * sigma,
                        "{minute}' {score:?} reds {reds:?}: futures {got:.4} vs closed form {want:.4}"
                    );
                }
                checked += 1;
            }
        }
        assert_eq!(checked, 50);
    }

    #[test]
    fn same_seed_same_futures_and_new_seq_new_paths() {
        let inp = input([1, 1], 55.0, Effects::ALL, 10_000);
        let a = simulate(&inp).unwrap();
        assert_eq!(a, simulate(&inp).unwrap());
        assert_eq!(a, simulate_sequential(&inp).unwrap());
        let mut other = inp.clone();
        other.seed = futures_seed("TEST", 2);
        assert_ne!(a, simulate(&other).unwrap());
        assert_ne!(futures_seed("TEST", 1), futures_seed("TEST", 2));
        assert_ne!(futures_seed("TEST", 1), futures_seed("TEST2", 1));
        assert!(futures_seed("TEST", 1) < 1 << 53);
    }

    #[test]
    fn sense_checks() {
        // 0-0 at 89' (four added minutes announced): a draw is very likely.
        let mut late = input([0, 0], 89.0, Effects::ALL, 50_000);
        late.minutes_left = remaining_minutes(89.0, 2, Some(4.0));
        let late = simulate(&late).unwrap();
        assert!(late.draw > 0.85, "0-0 at 89': draw {}", late.draw);
        // 3-0 at 60': the side three down almost never wins.
        let rout = simulate(&input([3, 0], 60.0, Effects::ALL, 50_000)).unwrap();
        let cb = rout.comeback.clone().unwrap();
        assert_eq!(cb.side, 1);
        assert!(cb.win < 0.02, "3-0 at 60': trailing side wins {}", cb.win);
        assert!(cb.avoid_defeat >= cb.win);
        // A red card lowers that side's win probability.
        let even = simulate(&input([0, 0], 30.0, Effects::ALL, 50_000)).unwrap();
        let mut red = input([0, 0], 30.0, Effects::ALL, 50_000);
        red.reds = [1, 0];
        let red = simulate(&red).unwrap();
        assert!(red.home < even.home - 0.03, "{} vs {}", red.home, even.home);
        // Momentum lifts the side on top.
        let mut push = input([0, 0], 70.0, Effects::ALL, 50_000);
        push.momentum = 0.8;
        push.field_tilt = 0.75;
        let push = simulate(&push).unwrap();
        let flat = simulate(&input([0, 0], 70.0, Effects::ALL, 50_000)).unwrap();
        assert!(push.next_goal.home > flat.next_goal.home + 0.01);
        // Game state: a side one down late scores more than the closed form expects.
        let behind = input([0, 1], 75.0, Effects::ALL, 50_000);
        let mut closed = behind.clone();
        closed.effects = Effects::NONE;
        let (with, without) = (simulate(&behind).unwrap(), simulate(&closed).unwrap());
        assert!(with.goals_left[0] > without.goals_left[0]);
        assert!(with.goals_left[1] < without.goals_left[1]);
    }

    #[test]
    fn output_is_consistent() {
        let f = simulate(&input([1, 0], 20.0, Effects::ALL, 20_000)).unwrap();
        assert_eq!(f.paths, 20_000);
        assert!((f.home + f.draw + f.away - 1.0).abs() < 1e-12);
        assert!((f.next_goal.home + f.next_goal.away + f.next_goal.none - 1.0).abs() < 1e-12);
        assert_eq!(f.scores.len(), TOP_SCORES);
        let listed: f64 = f.scores.iter().map(|s| s.p).sum();
        assert!((listed + f.other - 1.0).abs() < 1e-9);
        assert!(f.scores.windows(2).all(|w| w[0].p >= w[1].p));
        assert!(f.scores.iter().all(|s| s.home >= 1));
        let m = f.next_goal.median_minute.unwrap();
        assert!(m > 20.0 && m < 95.0);
    }

    #[test]
    fn finished_or_empty_futures() {
        let mut inp = input([2, 1], 96.0, Effects::ALL, 1000);
        inp.minutes_left = 0.0;
        let f = simulate(&inp).unwrap();
        assert_eq!((f.home, f.draw, f.away), (1.0, 0.0, 0.0));
        assert_eq!(
            f.scores,
            vec![ScoreChance {
                home: 2,
                away: 1,
                p: 1.0
            }]
        );
        assert_eq!(f.next_goal.none, 1.0);
        assert_eq!(f.next_goal.median_minute, None);
        inp.paths = 0;
        assert!(simulate(&inp).is_err());
        inp.paths = 10;
        inp.momentum = f64::NAN;
        assert!(simulate(&inp).is_err());
    }

    #[test]
    fn discipline_rate_follows_cards_and_fouls() {
        let avg = red_card_rate_90(2, 11, 90.0);
        assert!((avg - RED_CARDS_PER_90).abs() < 1e-12);
        assert!(red_card_rate_90(5, 20, 60.0) > avg);
        assert!(red_card_rate_90(0, 2, 60.0) < avg);
        assert_eq!(red_card_rate_90(0, 0, 0.0), RED_CARDS_PER_90);
    }

    /// Timings: `cargo test --release -p pitchlens-kernel --features parallel -- --ignored --nocapture`.
    #[test]
    #[ignore]
    fn timings() {
        for minute in [0.0, 80.0] {
            let inp = input([0, 0], minute, Effects::ALL, DEFAULT_PATHS);
            simulate(&inp).unwrap();
            let t = std::time::Instant::now();
            let reps = 20;
            for _ in 0..reps {
                simulate(&inp).unwrap();
            }
            let ms = t.elapsed().as_secs_f64() * 1000.0 / reps as f64;
            let t = std::time::Instant::now();
            simulate_sequential(&inp).unwrap();
            let seq = t.elapsed().as_secs_f64() * 1000.0;
            println!("100k paths from {minute}': {ms:.2} ms (one thread: {seq:.2} ms)");
        }
    }
}
