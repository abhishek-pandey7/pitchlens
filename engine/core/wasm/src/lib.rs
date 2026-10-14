//! WebAssembly bindings for the PitchLens kernel: the same Rust the engine runs, in the browser.
//!
//! Only conversion lives here (JS values to kernel types and back); all logic is in `pitchlens-kernel`.
//! The web app loads the package in a worker (`web/lib/kernel.worker.ts`), so builds never block the page.

use serde::Deserialize;
use wasm_bindgen::prelude::*;

use pitchlens_kernel::shape::Unit;
use pitchlens_kernel::tracking::{build, BuildError, Events, Side};

/// The crate version, so the page can say which kernel it loaded.
#[wasm_bindgen]
pub fn version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

/// One team, as `sim/tracking.py: _pack` describes it.
#[derive(Deserialize)]
struct SideInput {
    is_home: bool,
    players: Vec<usize>,
    anchors: Vec<(f64, f64)>,
    units: Vec<u8>,
    press: f64,
    width: f64,
}

/// A match packed for the tracking build: the JSON the engine serves at
/// `GET /matches/{id}/tracking-input` (event columns, sides, players' top speeds). Points are
/// `[x, y]` or `null` where Python packs NaN.
#[derive(Deserialize)]
struct TrackingInput {
    kind: Vec<u8>,
    flags: Vec<u8>,
    match_ms: Vec<i64>,
    period: Vec<i64>,
    team: Vec<i64>,
    player: Vec<i64>,
    related: Vec<i64>,
    recipient: Vec<i64>,
    duration_ms: Vec<i64>,
    start: Vec<Option<[f64; 2]>>,
    end: Vec<Option<[f64; 2]>>,
    top_speed_kmh: Vec<f64>,
    sides: Vec<SideInput>,
}

/// A built match: 5 Hz frames, row-major like the engine's NumPy arrays.
#[wasm_bindgen]
pub struct Tracking {
    ms: Vec<f64>,
    period: Vec<i8>,
    ball: Vec<f32>,
    xy: Vec<f32>,
    players: usize,
}

#[wasm_bindgen]
impl Tracking {
    /// Frame times in match milliseconds (exact: they stay far below 2^53).
    #[wasm_bindgen(getter)]
    pub fn ms(&self) -> Vec<f64> {
        self.ms.clone()
    }

    #[wasm_bindgen(getter)]
    pub fn period(&self) -> Vec<i8> {
        self.period.clone()
    }

    /// `frames x 3` (x, y, z).
    #[wasm_bindgen(getter)]
    pub fn ball(&self) -> Vec<f32> {
        self.ball.clone()
    }

    /// `frames x players x 2`, NaN while a player is off the pitch.
    #[wasm_bindgen(getter)]
    pub fn xy(&self) -> Vec<f32> {
        self.xy.clone()
    }

    #[wasm_bindgen(getter)]
    pub fn players(&self) -> usize {
        self.players
    }

    #[wasm_bindgen(getter)]
    pub fn frames(&self) -> usize {
        self.ms.len()
    }
}

fn error(e: BuildError) -> JsError {
    match e {
        BuildError::Key(msg) | BuildError::StopIteration(msg) | BuildError::Value(msg) => {
            JsError::new(&msg)
        }
    }
}

/// Build a match's tracking from its packed input, exactly as the engine does.
#[wasm_bindgen(js_name = buildTracking)]
pub fn build_tracking(input: JsValue) -> Result<Tracking, JsError> {
    let input: TrackingInput = serde_wasm_bindgen::from_value(input)?;
    let nan = [f64::NAN, f64::NAN];
    let start: Vec<[f64; 2]> = input.start.iter().map(|p| p.unwrap_or(nan)).collect();
    let end: Vec<[f64; 2]> = input.end.iter().map(|p| p.unwrap_or(nan)).collect();
    let events = Events {
        kind: &input.kind,
        flags: &input.flags,
        match_ms: &input.match_ms,
        period: &input.period,
        team: &input.team,
        player: &input.player,
        related: &input.related,
        recipient: &input.recipient,
        duration_ms: &input.duration_ms,
        start: &start,
        end: &end,
    };
    let n = events.len();
    let columns = [
        events.flags.len(),
        events.match_ms.len(),
        events.period.len(),
        events.team.len(),
        events.player.len(),
        events.related.len(),
        events.recipient.len(),
        events.duration_ms.len(),
        start.len(),
        end.len(),
    ];
    if columns.iter().any(|&l| l != n) {
        return Err(JsError::new("event columns differ in length"));
    }
    let sides = input
        .sides
        .into_iter()
        .map(|s| {
            let units = s
                .units
                .iter()
                .map(|&u| Unit::from_code(u).ok_or_else(|| JsError::new(&format!("unit code {u}"))))
                .collect::<Result<Vec<_>, _>>()?;
            Ok(Side {
                is_home: s.is_home,
                players: s.players,
                anchors: s.anchors,
                units,
                press: s.press,
                width: s.width,
            })
        })
        .collect::<Result<Vec<_>, JsError>>()?;
    let built = build(&events, sides, &input.top_speed_kmh).map_err(error)?;
    Ok(Tracking {
        ms: built.ms.iter().map(|&m| m as f64).collect(),
        period: built.period,
        ball: built.ball,
        xy: built.xy,
        players: input.top_speed_kmh.len(),
    })
}
