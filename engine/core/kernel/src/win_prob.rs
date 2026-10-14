//! In-play win probability: a port of `pitchlens/interpret/win_prob.py`, bit for bit.
//!
//! Remaining goals for each side follow independent Poisson distributions over a 0..=10 grid. The
//! arithmetic follows Python's order of operations exactly: `float ** int` and `float ** float` are the C
//! library's `pow` (CPython's `float_pow` calls it for every case these functions reach), `math.exp` is
//! the C library's `exp`, and `math.factorial(k)` is exact, so dividing by it is dividing by the same
//! double. Rust's `powf` and `exp` call the same C library functions, so on one platform the results
//! are identical (`engine/tests/unit/test_core_parity_win_prob.py` checks every state of several
//! matches). Across platforms the C libraries may round `pow`/`exp` differently in the last bit.

pub const MAX_GOALS: usize = 10;
pub const LIVE_WEIGHT_AT_FULL_TIME: f64 = 0.5;
pub const RED_CARD_OWN: f64 = 0.7;
pub const RED_CARD_OPPONENT: f64 = 1.2;
pub const DEFAULT_ADDED_MINUTES: f64 = 5.0;

/// `math.factorial(k)` for k in 0..=MAX_GOALS, as doubles (all exact).
const FACTORIAL: [f64; MAX_GOALS + 1] = [
    1.0, 1.0, 2.0, 6.0, 24.0, 120.0, 720.0, 5040.0, 40320.0, 362880.0, 3628800.0,
];

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct WinProbability {
    pub home: f64,
    pub draw: f64,
    pub away: f64,
}

/// Python's `x ** n` for a float `x` and an int `n >= 0`: 1.0 for `n == 0`, else `pow(x, n)`.
pub fn py_pow_int(x: f64, n: u32) -> f64 {
    if n == 0 {
        1.0
    } else {
        x.powf(n as f64)
    }
}

fn poisson(k: usize, lam: f64) -> f64 {
    (-lam).exp() * py_pow_int(lam, k as u32) / FACTORIAL[k]
}

/// Python's `max(0.0, v)`: `v` only when it is greater (equal values keep the first argument).
fn py_max0(v: f64) -> f64 {
    if v > 0.0 {
        v
    } else {
        0.0
    }
}

/// Blend the pre-match xG/90 with the live xG/90 rate (the live rate earns weight slowly).
pub fn scoring_rate_per_90(prior: f64, xg: f64, minutes_played: f64) -> f64 {
    if minutes_played <= 1.0 {
        return prior;
    }
    let live = xg / minutes_played * 90.0;
    let share = minutes_played / 90.0;
    let share = if share <= 1.0 { share } else { 1.0 }; // Python's min(share, 1.0)
    let w = LIVE_WEIGHT_AT_FULL_TIME * share.powf(1.5);
    (1.0 - w) * prior + w * live
}

/// Minutes left including stoppage time (an estimate until the added time is announced).
pub fn remaining_minutes(clock_minute: f64, period: u32, added_minutes: Option<f64>) -> f64 {
    let added = added_minutes.unwrap_or(DEFAULT_ADDED_MINUTES);
    if period == 1 {
        return py_max0(45.0 - clock_minute) + 45.0 + added;
    }
    py_max0(90.0 + added - clock_minute)
}

/// The closed-form win probability: the same numbers as `win_prob.win_probability`.
pub fn win_probability(
    home_goals: i64,
    away_goals: i64,
    home_rate_90: f64,
    away_rate_90: f64,
    minutes_left: f64,
    home_reds: u32,
    away_reds: u32,
) -> WinProbability {
    if minutes_left <= 0.0 {
        let diff = home_goals - away_goals;
        let b = |c: bool| if c { 1.0 } else { 0.0 };
        return WinProbability {
            home: b(diff > 0),
            draw: b(diff == 0),
            away: b(diff < 0),
        };
    }
    let lam_h = home_rate_90
        * py_pow_int(RED_CARD_OWN, home_reds)
        * py_pow_int(RED_CARD_OPPONENT, away_reds)
        * minutes_left
        / 90.0;
    let lam_a = away_rate_90
        * py_pow_int(RED_CARD_OWN, away_reds)
        * py_pow_int(RED_CARD_OPPONENT, home_reds)
        * minutes_left
        / 90.0;
    let mut ph = [0.0; MAX_GOALS + 1];
    let mut pa = [0.0; MAX_GOALS + 1];
    for k in 0..=MAX_GOALS {
        ph[k] = poisson(k, lam_h);
        pa[k] = poisson(k, lam_a);
    }
    let (mut home, mut draw, mut away) = (0.0, 0.0, 0.0);
    for (i, p_i) in ph.iter().enumerate() {
        for (j, p_j) in pa.iter().enumerate() {
            let diff = home_goals + i as i64 - (away_goals + j as i64);
            let p = p_i * p_j;
            if diff > 0 {
                home += p;
            } else if diff == 0 {
                draw += p;
            } else {
                away += p;
            }
        }
    }
    let total = home + draw + away;
    WinProbability {
        home: home / total,
        draw: draw / total,
        away: away / total,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finished_match_is_certain() {
        let wp = win_probability(2, 1, 1.4, 1.2, 0.0, 0, 0);
        assert_eq!((wp.home, wp.draw, wp.away), (1.0, 0.0, 0.0));
    }

    #[test]
    fn probabilities_sum_to_one_and_reds_hurt() {
        let wp = win_probability(0, 0, 1.4, 1.2, 60.0, 0, 0);
        assert!((wp.home + wp.draw + wp.away - 1.0).abs() < 1e-12);
        let red = win_probability(0, 0, 1.4, 1.2, 60.0, 1, 0);
        assert!(red.home < wp.home);
    }

    #[test]
    fn remaining_minutes_matches_python() {
        assert_eq!(remaining_minutes(0.0, 1, None), 95.0);
        assert_eq!(remaining_minutes(47.0, 1, None), 50.0);
        assert_eq!(remaining_minutes(92.0, 2, Some(4.0)), 2.0);
        assert_eq!(remaining_minutes(99.0, 2, Some(4.0)), 0.0);
    }
}
