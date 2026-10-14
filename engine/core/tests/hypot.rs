//! The two hypots the tracking build uses, against values captured from Python
//! (`python scripts/capture_core_cases.py` writes tests/data/hypot_cases.txt).

use pitchlens_core::tracking::python_hypot;

fn cases() -> Vec<[f64; 4]> {
    include_str!("data/hypot_cases.txt")
        .lines()
        .filter(|l| l.starts_with("hypot "))
        .map(|l| {
            let f: Vec<f64> = l
                .split_whitespace()
                .skip(1)
                .map(|s| s.parse().unwrap())
                .collect();
            [f[0], f[1], f[2], f[3]]
        })
        .collect()
}

#[test]
fn python_hypot_is_math_hypot_bit_for_bit() {
    let cases = cases();
    assert!(cases.len() > 500);
    for [x, y, want, _] in cases {
        let got = python_hypot(x, y);
        assert_eq!(
            got.to_bits(),
            want.to_bits(),
            "math.hypot({x}, {y}): got {got}, want {want}"
        );
    }
}

#[test]
fn rust_hypot_is_within_one_ulp_of_numpy() {
    // np.hypot and f64::hypot both call the platform C library's hypot; on Linux (glibc) they are the
    // same function, elsewhere the libraries may round differently in the last bit.
    for [x, y, _, want] in cases() {
        let got = x.hypot(y);
        let ulps = (got.to_bits() as i64 - want.to_bits() as i64).abs();
        assert!(ulps <= 1, "hypot({x}, {y}): got {got}, want {want}");
    }
}
