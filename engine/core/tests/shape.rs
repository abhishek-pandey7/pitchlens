//! `team_shape` against values captured from `pitchlens/sim/pitch.py`
//! (`python scripts/capture_core_cases.py` writes tests/data/shape_cases.txt).

use pitchlens_core::shape::{team_shape, Unit, XY};

struct Case {
    label: String,
    in_possession: bool,
    press: f64,
    width: f64,
    ball: XY,
    anchors: Vec<XY>,
    units: Vec<Unit>,
    expected: Vec<XY>,
}

fn num(s: &str) -> f64 {
    s.parse().unwrap_or_else(|_| panic!("not a number: {s}"))
}

fn cases() -> Vec<Case> {
    let mut cases: Vec<Case> = Vec::new();
    for line in include_str!("data/shape_cases.txt").lines() {
        let f: Vec<&str> = line.split_whitespace().collect();
        match f.first() {
            Some(&"case") => cases.push(Case {
                label: line.to_string(),
                in_possession: f[2] == "1",
                press: num(f[3]),
                width: num(f[4]),
                ball: (num(f[5]), num(f[6])),
                anchors: Vec::new(),
                units: Vec::new(),
                expected: Vec::new(),
            }),
            Some(&"slot") => {
                let case = cases.last_mut().expect("a slot follows a case");
                case.anchors.push((num(f[1]), num(f[2])));
                case.units
                    .push(Unit::from_code(f[3].parse().unwrap()).expect("unit code"));
                case.expected.push((num(f[4]), num(f[5])));
            }
            _ => {}
        }
    }
    cases
}

#[test]
fn team_shape_matches_python_for_every_formation_possession_and_press() {
    let cases = cases();
    // 4 formations x 2 possession states x 3 press x 2 width x 5 ball positions.
    assert_eq!(cases.len(), 240);
    let mut out = Vec::new();
    let mut worst = 0.0f64;
    for case in &cases {
        team_shape(
            &case.anchors,
            &case.units,
            case.ball,
            case.in_possession,
            case.press,
            case.width,
            &mut out,
        );
        assert_eq!(out.len(), 11, "{}", case.label);
        for (got, want) in out.iter().zip(&case.expected) {
            let diff = (got.0 - want.0).abs().max((got.1 - want.1).abs());
            worst = worst.max(diff);
            assert!(diff <= 1e-9, "{}: got {got:?}, want {want:?}", case.label);
        }
    }
    // The port keeps Python's operation order, so it is in fact exact.
    assert_eq!(worst, 0.0);
}
