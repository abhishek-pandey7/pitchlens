//! Hot paths of the PitchLens engine, called from Python (`pitchlens/core.py`).
//!
//! Each function here has a pure-Python twin that stays the reference implementation: parity tests in
//! `engine/tests/unit/test_core_parity.py` compare the two, and `PITCHLENS_CORE=python` switches back.

use pyo3::prelude::*;

mod frames;

/// The crate version, so the engine can report which core it loaded.
#[pyfunction]
fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

#[pymodule]
fn pitchlens_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_function(wrap_pyfunction!(frames::frames_json, m)?)?;
    Ok(())
}
