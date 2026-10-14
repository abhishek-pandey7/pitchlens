use numpy::{PyReadonlyArray1, PyReadonlyArray2, PyReadonlyArray3};
use pyo3::exceptions::{PyIndexError, PyValueError};
use pyo3::prelude::*;

use pitchlens_kernel::physical::*;

// ------------------------------------------------------------------------------------------ Python class

type AlertRow = (&'static str, String, i64, f64, f64);

/// `apply_range` lets other Python threads run while it works on at least this many frames.
const RELEASE_GIL_FRAMES: usize = 2000;
type PlayerRow = (String, f64, f64, f64, i64, f64, f64);

/// Rust twin of `pitchlens.interpret.physical.PhysicalTracker`. Alerts come back as tuples
/// `(kind, player_id, match_ms, value, threshold)`; Python builds the `PhysicalAlert` models.
#[pyclass(name = "PhysicalTracker", module = "pitchlens_core")]
#[derive(Default)]
pub struct PyPhysicalTracker {
    inner: Tracker,
}

impl PyPhysicalTracker {
    fn rows(&self, alerts: Vec<Alert>) -> Vec<AlertRow> {
        alerts
            .into_iter()
            .map(|a| {
                let id = self.inner.players[a.player].player_id.clone();
                (a.kind.name(), id, a.match_ms, a.value, a.threshold)
            })
            .collect()
    }
}

fn player_row(p: &Player) -> PlayerRow {
    (
        p.player_id.clone(),
        p.distance_m,
        p.hsr_m,
        p.sprint_m,
        p.sprints,
        p.top_speed_kmh,
        p.speed_kmh,
    )
}

#[pymethods]
impl PyPhysicalTracker {
    #[new]
    fn new() -> Self {
        Self::default()
    }

    /// One `TrackingFrame` (the live path). `period` is the match period the frame belongs to.
    fn apply(&mut self, frame: &Bound<'_, PyAny>, period: i64) -> PyResult<Vec<AlertRow>> {
        let t: i64 = frame.getattr("match_ms")?.extract()?;
        let ball = frame.getattr("ball")?;
        let (bx, by): (f64, f64) = (ball.getattr("x")?.extract()?, ball.getattr("y")?.extract()?);
        self.inner.begin_frame(period, t, bx, by);
        let mut alerts = Vec::new();
        for tp in frame.getattr("players")?.try_iter()? {
            let tp = tp?;
            let id = tp.getattr("player_id")?;
            let i = self
                .inner
                .player_index(id.downcast::<pyo3::types::PyString>()?.to_str()?);
            let (x, y): (f64, f64) = (tp.getattr("x")?.extract()?, tp.getattr("y")?.extract()?);
            self.inner.step(i, t, x, y, &mut alerts);
        }
        Ok(self.rows(alerts))
    }

    /// Frames `start..end` straight from a `Tracking`'s arrays, with the same results as calling
    /// `apply(tracking.frame(i), period)` for each. `period` is the per-frame array (`tracking.period`)
    /// or one int for the whole range.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (ms, period, ball, xy, player_ids, start, end))]
    fn apply_range(
        &mut self,
        py: Python<'_>,
        ms: PyReadonlyArray1<'_, i64>,
        period: &Bound<'_, PyAny>,
        ball: PyReadonlyArray2<'_, f32>,
        xy: PyReadonlyArray3<'_, f32>,
        player_ids: Vec<String>,
        start: usize,
        end: usize,
    ) -> PyResult<Vec<AlertRow>> {
        let (ms, ball, xy) = (ms.as_array(), ball.as_array(), xy.as_array());
        let n = ms.len();
        if start > end || end > n {
            return Err(PyIndexError::new_err(format!(
                "frames {start}..{end} out of 0..{n}"
            )));
        }
        if ball.shape() != [n, 3] || xy.shape() != [n, player_ids.len(), 2] {
            return Err(PyValueError::new_err(format!(
                "shapes do not line up: ms {:?}, ball {:?}, xy {:?}, {} player ids",
                ms.shape(),
                ball.shape(),
                xy.shape(),
                player_ids.len()
            )));
        }
        let per_frame;
        let period = if let Ok(p) = period.extract::<i64>() {
            Period::Fixed(p)
        } else {
            per_frame = period.extract::<PyReadonlyArray1<'_, i8>>()?;
            let arr = per_frame.as_array();
            if arr.len() != n {
                return Err(PyValueError::new_err(
                    "period must have one entry per frame",
                ));
            }
            Period::PerFrame(arr)
        };
        let (inner, ids) = (&mut self.inner, &player_ids);
        let run = || apply_range(inner, ms, period, ball, xy, ids, (start, end));
        // A seek applies a few frames per event: releasing the GIL would cost more than it frees.
        let alerts = if end - start >= RELEASE_GIL_FRAMES {
            py.allow_threads(run)
        } else {
            run()
        };
        Ok(self.rows(alerts))
    }

    /// Every player seen so far, in first-seen order:
    /// `(player_id, distance_m, hsr_m, sprint_m, sprints, top_speed_kmh, speed_kmh)`.
    fn players(&self) -> Vec<PlayerRow> {
        self.inner.players.iter().map(player_row).collect()
    }

    /// One player's row, creating an empty one if the player hasn't been seen (as Python does).
    fn player(&mut self, player_id: &str) -> PlayerRow {
        let i = self.inner.player_index(player_id);
        player_row(&self.inner.players[i])
    }

    #[getter]
    fn ball_speed_kmh(&self) -> f64 {
        self.inner.ball_speed_kmh
    }

    #[getter]
    fn max_ball_speed_kmh(&self) -> f64 {
        self.inner.max_ball_speed_kmh
    }
}

/// The thresholds the Rust tracker was built with, so Python can check they match its own.
#[pyfunction]
pub fn physical_constants() -> (f64, f64, i64, i64, Vec<f64>, Vec<f64>, Vec<i64>) {
    (
        HSR_KMH,
        SPRINT_KMH,
        SPRINT_MIN_MS,
        SPEED_WINDOW_MS,
        TOP_SPEED_THRESHOLDS_KMH.to_vec(),
        DISTANCE_THRESHOLDS_KM.to_vec(),
        SPRINT_COUNT_THRESHOLDS.to_vec(),
    )
}
