//! `BallPath` (and its `np.interp`) against values captured from `pitchlens/sim/tracking.py`
//! (`python scripts/capture_core_cases.py` writes tests/data/ball_cases.txt).

use pitchlens_kernel::tracking::{BallEvent, BallPath};

struct Set {
    name: String,
    events: Vec<BallEvent>,
    queries: Vec<Vec<[f64; 4]>>, // ms, expected x, y, z
}

fn num(s: &str) -> f64 {
    s.parse().unwrap_or_else(|_| panic!("not a number: {s}"))
}

fn point(x: &str, y: &str) -> Option<(f64, f64)> {
    let (x, y) = (num(x), num(y));
    (!x.is_nan()).then_some((x, y))
}

fn sets() -> Vec<Set> {
    let mut sets: Vec<Set> = Vec::new();
    for line in include_str!("data/ball_cases.txt").lines() {
        let f: Vec<&str> = line.split_whitespace().collect();
        let set = sets.last_mut();
        match f.first() {
            Some(&"set") => sets.push(Set {
                name: f[1].to_string(),
                events: Vec::new(),
                queries: Vec::new(),
            }),
            Some(&"event") => set.expect("an event follows a set").events.push(BallEvent {
                match_ms: f[1].parse().unwrap(),
                start: point(f[2], f[3]),
                end: point(f[4], f[5]),
                duration_ms: f[6].parse().unwrap(),
                high: f[7] == "1",
            }),
            Some(&"query") => set.expect("a query follows a set").queries.push(Vec::new()),
            Some(&"at") => {
                let row = [num(f[1]), num(f[2]), num(f[3]), num(f[4])];
                set.expect("a query follows a set")
                    .queries
                    .last_mut()
                    .expect("in a query")
                    .push(row);
            }
            _ => {}
        }
    }
    sets
}

#[test]
fn ball_path_matches_python_exactly() {
    let sets = sets();
    assert_eq!(sets.len(), 2);
    for set in &sets {
        let path = BallPath::new(set.events.iter().copied());
        assert_eq!(
            set.queries.len(),
            2,
            "{}: a dense and a sparse query",
            set.name
        );
        for rows in &set.queries {
            let ms: Vec<f64> = rows.iter().map(|r| r[0]).collect();
            let got = path.at(&ms).expect("a non-empty path");
            for (i, row) in rows.iter().enumerate() {
                for axis in 0..3 {
                    assert_eq!(
                        got[axis][i].to_bits(),
                        row[axis + 1].to_bits(),
                        "{} at {} ms, axis {axis}: got {}, want {}",
                        set.name,
                        row[0],
                        got[axis][i],
                        row[axis + 1]
                    );
                }
            }
        }
    }
}

#[test]
fn an_empty_path_is_an_error_like_numpy() {
    let path = BallPath::new(std::iter::empty());
    assert!(path.at(&[0.0]).is_err());
}
