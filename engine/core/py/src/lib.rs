//! Python bindings for the PitchLens kernel: the `pitchlens_core` module (`pitchlens/core.py`).
//!
//! Only conversion lives here (NumPy arrays and Python objects to kernel types, kernel errors to
//! Python exceptions); all logic is in `pitchlens-kernel`.

use pyo3::prelude::*;

mod frames;
mod physical;
mod tracking;

/// The crate version, so the engine can report which core it loaded.
#[pyfunction]
fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

#[pymodule]
fn pitchlens_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_function(wrap_pyfunction!(tracking::build_tracking, m)?)?;
    m.add_function(wrap_pyfunction!(frames::frames_json, m)?)?;
    m.add_class::<physical::PyPhysicalTracker>()?;
    m.add_function(wrap_pyfunction!(physical::physical_constants, m)?)?;
    Ok(())
}
