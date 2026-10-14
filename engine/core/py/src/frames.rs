use numpy::{PyReadonlyArray1, PyReadonlyArray2, PyReadonlyArray3};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use pitchlens_kernel::frames::{FrameError, Frames};

fn to_py(e: FrameError) -> PyErr {
    match e {
        FrameError::Shape(msg) => PyValueError::new_err(msg),
        FrameError::Value { frame, what } => {
            PyValueError::new_err(format!("frame {frame}: {what}"))
        }
    }
}

/// Tracking frames `start..end` (clamped to the match) as compact JSON text, one string per frame, equal
/// to `json.dumps(Tracking.frame(i).model_dump(mode="json"), separators=(",", ":"), ensure_ascii=False)`.
#[pyfunction]
#[allow(clippy::too_many_arguments)] // mirrors the Python call: one argument per Tracking field
#[pyo3(signature = (match_id, ms, ball, xy, player_ids, start, end))]
pub fn frames_json(
    py: Python<'_>,
    match_id: &str,
    ms: PyReadonlyArray1<'_, i64>,
    ball: PyReadonlyArray2<'_, f32>,
    xy: PyReadonlyArray3<'_, f32>,
    player_ids: Vec<String>,
    start: usize,
    end: usize,
) -> PyResult<Vec<String>> {
    let frames = Frames {
        match_id,
        ms: ms.as_array(),
        ball: ball.as_array(),
        xy: xy.as_array(),
        player_ids: &player_ids,
    };
    // Pure Rust over borrowed arrays: let other Python threads run (clips are served from a thread).
    py.allow_threads(|| frames.render(start, end))
        .map_err(to_py)
}
