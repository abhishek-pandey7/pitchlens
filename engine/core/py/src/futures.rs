use std::time::Instant;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use pitchlens_kernel::futures::{self, Effects, FuturesError, FuturesInput};
use pitchlens_kernel::win_prob;

fn to_py(e: FuturesError) -> PyErr {
    match e {
        FuturesError::Input(msg) => PyValueError::new_err(msg),
    }
}

/// `win_prob.win_probability` as `(home, draw, away)`, bit for bit.
#[pyfunction]
#[pyo3(signature = (home_goals, away_goals, home_rate_90, away_rate_90, minutes_left, home_reds=0, away_reds=0))]
pub fn win_probability(
    home_goals: i64,
    away_goals: i64,
    home_rate_90: f64,
    away_rate_90: f64,
    minutes_left: f64,
    home_reds: u32,
    away_reds: u32,
) -> (f64, f64, f64) {
    let wp = win_prob::win_probability(
        home_goals,
        away_goals,
        home_rate_90,
        away_rate_90,
        minutes_left,
        home_reds,
        away_reds,
    );
    (wp.home, wp.draw, wp.away)
}

/// The run's seed for a match and an event sequence number (53 bits).
#[pyfunction]
pub fn futures_seed(match_id: &str, seq: u64) -> u64 {
    futures::futures_seed(match_id, seq)
}

/// Simulate the rest of the match; the result as a dict shaped like the `Futures` schema (without the
/// match fields, which Python adds). `ms` is the wall time of the simulation.
#[pyfunction]
#[allow(clippy::too_many_arguments)] // one argument per FuturesInput field
#[pyo3(signature = (
    score, minute, minutes_left, rate_90, reds, momentum, field_tilt, red_rate_90, paths, seed,
    game_state=true, momentum_effect=true, discipline=true
))]
pub fn simulate_futures<'py>(
    py: Python<'py>,
    score: [u32; 2],
    minute: f64,
    minutes_left: f64,
    rate_90: [f64; 2],
    reds: [u32; 2],
    momentum: f64,
    field_tilt: f64,
    red_rate_90: [f64; 2],
    paths: u32,
    seed: u64,
    game_state: bool,
    momentum_effect: bool,
    discipline: bool,
) -> PyResult<Bound<'py, PyDict>> {
    let input = FuturesInput {
        score,
        minute,
        minutes_left,
        rate_90,
        reds,
        momentum,
        field_tilt,
        red_rate_90,
        effects: Effects {
            game_state,
            momentum: momentum_effect,
            discipline,
        },
        paths,
        seed,
    };
    let start = Instant::now();
    let f = py
        .allow_threads(|| futures::simulate(&input))
        .map_err(to_py)?;
    let ms = start.elapsed().as_secs_f64() * 1000.0;

    let out = PyDict::new(py);
    out.set_item("paths", f.paths)?;
    out.set_item("home", f.home)?;
    out.set_item("draw", f.draw)?;
    out.set_item("away", f.away)?;
    let scores: Vec<(u32, u32, f64)> = f.scores.iter().map(|s| (s.home, s.away, s.p)).collect();
    out.set_item("scores", scores)?;
    out.set_item("other", f.other)?;
    let comeback = f.comeback.map(|c| {
        (
            if c.side == 0 { "home" } else { "away" },
            c.avoid_defeat,
            c.win,
        )
    });
    out.set_item("comeback", comeback)?;
    let ng = f.next_goal;
    out.set_item("next_goal", (ng.home, ng.away, ng.none, ng.median_minute))?;
    out.set_item("goals_left", (f.goals_left[0], f.goals_left[1]))?;
    out.set_item("ms", ms)?;
    Ok(out)
}

/// The model's constants, so the NumPy fallback can check it uses the same numbers.
#[pyfunction]
pub fn futures_constants(py: Python<'_>) -> PyResult<Bound<'_, PyDict>> {
    use futures::*;
    let out = PyDict::new(py);
    let floats = [
        ("STEP_MINUTES", STEP_MINUTES),
        ("GAME_STATE_TRAILING_BOOST", GAME_STATE_TRAILING_BOOST),
        ("GAME_STATE_LEADING_DROP", GAME_STATE_LEADING_DROP),
        ("GAME_STATE_TWO_GOAL_SCALE", GAME_STATE_TWO_GOAL_SCALE),
        ("GAME_STATE_RAMP_MINUTES", GAME_STATE_RAMP_MINUTES),
        ("MOMENTUM_LIFT", MOMENTUM_LIFT),
        ("MOMENTUM_WEIGHT", MOMENTUM_WEIGHT),
        ("TILT_WEIGHT", TILT_WEIGHT),
        ("MOMENTUM_HALF_LIFE_MINUTES", MOMENTUM_HALF_LIFE_MINUTES),
        ("RED_CARDS_PER_90", RED_CARDS_PER_90),
        ("CARDS_PER_90", CARDS_PER_90),
        ("FOULS_PER_90", FOULS_PER_90),
        ("DISCIPLINE_PRIOR_MINUTES", DISCIPLINE_PRIOR_MINUTES),
        ("DISCIPLINE_MIN_INDEX", DISCIPLINE_MIN_INDEX),
        ("DISCIPLINE_MAX_INDEX", DISCIPLINE_MAX_INDEX),
    ];
    for (name, value) in floats {
        out.set_item(name, value)?;
    }
    out.set_item("MAX_REDS", MAX_REDS)?;
    out.set_item("TOP_SCORES", TOP_SCORES)?;
    out.set_item("DEFAULT_PATHS", DEFAULT_PATHS)?;
    out.set_item("MAX_PATHS", MAX_PATHS)?;
    Ok(out)
}

/// `futures::red_card_rate_90`, for the parity test of the Python twin.
#[pyfunction]
pub fn red_card_rate_90(cards: u32, fouls: u32, minutes_played: f64) -> f64 {
    futures::red_card_rate_90(cards, fouls, minutes_played)
}
