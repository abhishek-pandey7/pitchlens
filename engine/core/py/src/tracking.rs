use numpy::{PyArray1, PyArrayMethods, PyReadonlyArray1, PyReadonlyArray2, PyUntypedArrayMethods};
use pyo3::exceptions::{PyKeyError, PyStopIteration, PyValueError};
use pyo3::prelude::*;

use pitchlens_kernel::shape::Unit;
use pitchlens_kernel::tracking::*;

fn to_py(err: BuildError) -> PyErr {
    match err {
        BuildError::Key(msg) => PyKeyError::new_err(msg),
        BuildError::StopIteration(msg) => PyStopIteration::new_err(msg),
        BuildError::Value(msg) => PyValueError::new_err(msg),
    }
}

type SideTuple = (bool, Vec<usize>, Vec<(f64, f64)>, Vec<u8>, f64, f64);
type Arrays<'py> = (
    Bound<'py, PyArray1<i64>>,
    Bound<'py, PyArray1<i8>>,
    Bound<'py, numpy::PyArray2<f32>>,
    Bound<'py, numpy::PyArray3<f32>>,
);

fn points<'a>(name: &str, a: &'a PyReadonlyArray2<'_, f64>) -> PyResult<&'a [[f64; 2]]> {
    let shape = a.shape();
    if shape.len() != 2 || shape[1] != 2 {
        return Err(PyValueError::new_err(format!(
            "{name} must have shape (E, 2)"
        )));
    }
    let flat = a.as_slice()?;
    let (pairs, rest) = flat.as_chunks::<2>();
    debug_assert!(rest.is_empty());
    Ok(pairs)
}

/// Build a match's tracking from packed events (see `sim/tracking.py: _pack`). Returns
/// `(ms int64[T], period int8[T], ball float32[T, 3], xy float32[T, N, 2])`.
#[pyfunction]
#[pyo3(signature = (*, kind, flags, match_ms, period, team, player, related, recipient, duration_ms, start, end, top_speed_kmh, sides))]
#[allow(clippy::too_many_arguments)]
pub fn build_tracking<'py>(
    py: Python<'py>,
    kind: PyReadonlyArray1<'py, u8>,
    flags: PyReadonlyArray1<'py, u8>,
    match_ms: PyReadonlyArray1<'py, i64>,
    period: PyReadonlyArray1<'py, i64>,
    team: PyReadonlyArray1<'py, i64>,
    player: PyReadonlyArray1<'py, i64>,
    related: PyReadonlyArray1<'py, i64>,
    recipient: PyReadonlyArray1<'py, i64>,
    duration_ms: PyReadonlyArray1<'py, i64>,
    start: PyReadonlyArray2<'py, f64>,
    end: PyReadonlyArray2<'py, f64>,
    top_speed_kmh: PyReadonlyArray1<'py, f64>,
    sides: Vec<SideTuple>,
) -> PyResult<Arrays<'py>> {
    let events = Events {
        kind: kind.as_slice()?,
        flags: flags.as_slice()?,
        match_ms: match_ms.as_slice()?,
        period: period.as_slice()?,
        team: team.as_slice()?,
        player: player.as_slice()?,
        related: related.as_slice()?,
        recipient: recipient.as_slice()?,
        duration_ms: duration_ms.as_slice()?,
        start: points("start", &start)?,
        end: points("end", &end)?,
    };
    let n = events.len();
    let lengths = [
        events.flags.len(),
        events.match_ms.len(),
        events.period.len(),
        events.team.len(),
        events.player.len(),
        events.related.len(),
        events.recipient.len(),
        events.duration_ms.len(),
        events.start.len(),
        events.end.len(),
    ];
    if lengths.iter().any(|&l| l != n) {
        return Err(PyValueError::new_err("event columns differ in length"));
    }
    let sides = sides
        .into_iter()
        .map(|(is_home, players, anchors, units, press, width)| {
            let units = units
                .into_iter()
                .map(|u| {
                    Unit::from_code(u)
                        .ok_or_else(|| PyValueError::new_err(format!("unit code {u}")))
                })
                .collect::<PyResult<Vec<_>>>()?;
            Ok(Side {
                is_home,
                players,
                anchors,
                units,
                press,
                width,
            })
        })
        .collect::<PyResult<Vec<_>>>()?;
    let speeds = top_speed_kmh.as_slice()?;
    let n_players = speeds.len();
    let built = py
        .allow_threads(|| build(&events, sides, speeds))
        .map_err(to_py)?;
    let t = built.ms.len();
    Ok((
        PyArray1::from_vec(py, built.ms),
        PyArray1::from_vec(py, built.period),
        PyArray1::from_vec(py, built.ball).reshape([t, 3])?,
        PyArray1::from_vec(py, built.xy).reshape([t, n_players, 2])?,
    ))
}
