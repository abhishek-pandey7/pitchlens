//! The PitchLens kernel: tracking build, physical metrics, frame JSON, win probability and futures, in
//! pure Rust.
//!
//! No Python and no JavaScript in here: the `pitchlens-py` crate binds it to Python (PyO3) and
//! `pitchlens-wasm` to the browser. Each function has a pure-Python twin in the engine that stays the
//! reference implementation; parity tests in `engine/tests/unit/test_core_parity*.py` compare the two.

pub mod frames;
pub mod futures;
pub mod physical;
pub mod shape;
pub mod tracking;
pub mod win_prob;
