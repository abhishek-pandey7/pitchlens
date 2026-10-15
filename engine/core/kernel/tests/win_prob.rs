//! The win probability against values captured from Python (`python scripts/capture_core_cases.py`
//! writes tests/data/win_prob_cases.txt): every state of a match with a red card, plus what-ifs.
//!
//! Rust and CPython both take `pow` and `exp` from the platform C library, so on one platform they agree
//! bit for bit (engine/tests/unit/test_core_parity_win_prob.py checks that against the live Python). The
//! captured values may come from another platform's library, whose `pow` can round differently in the
//! last place, so this file checks a bound far below anything a probability shown to fans could notice.

use pitchlens_kernel::win_prob::win_probability;

const BOUND: f64 = 1e-14;

#[test]
fn win_probability_matches_python_captures() {
    let mut checked = 0;
    for line in include_str!("data/win_prob_cases.txt").lines() {
        if !line.starts_with("wp ") {
            continue;
        }
        let f: Vec<&str> = line.split_whitespace().skip(1).collect();
        let int = |i: usize| f[i].parse::<i64>().unwrap();
        let num = |i: usize| f[i].parse::<f64>().unwrap();
        let wp = win_probability(
            int(0),
            int(1),
            num(2),
            num(3),
            num(4),
            int(5) as u32,
            int(6) as u32,
        );
        for (got, want) in [(wp.home, num(7)), (wp.draw, num(8)), (wp.away, num(9))] {
            assert!(
                (got - want).abs() <= BOUND,
                "{line}: got {got}, want {want}"
            );
        }
        checked += 1;
    }
    assert!(checked > 500);
}
