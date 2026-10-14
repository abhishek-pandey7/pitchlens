//! Team shape and coordinate helpers: a line-for-line port of `pitchlens/sim/pitch.py`
//! (`team_shape`, `clamp`, `mirror`).
//!
//! Every expression keeps the Python operation order, so results are bit-identical to the reference
//! (Rust never fuses multiply-adds on its own). `py_min` / `py_max` copy Python's `min` / `max`
//! tie-breaking: the first argument wins unless the second is strictly smaller / larger.

pub const PITCH_LENGTH: f64 = 105.0;
pub const PITCH_WIDTH: f64 = 68.0;
pub const CENTRE: XY = (PITCH_LENGTH / 2.0, PITCH_WIDTH / 2.0);

pub type XY = (f64, f64);

/// The unit a shape slot belongs to. Python packs it from the slot's `Position` (`pitch._unit`), so
/// the Rust side never needs to know the position names.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Unit {
    Gk,
    Def,
    Mid,
    Fwd,
}

impl Unit {
    /// 0 = GK, 1 = defender, 2 = midfielder, 3 = forward (the codes `sim/tracking.py` packs).
    pub fn from_code(code: u8) -> Option<Unit> {
        match code {
            0 => Some(Unit::Gk),
            1 => Some(Unit::Def),
            2 => Some(Unit::Mid),
            3 => Some(Unit::Fwd),
            _ => None,
        }
    }

    /// `POSSESSION_MAX_X`: units never push beyond these lines in possession (team frame).
    fn possession_max_x(self) -> f64 {
        match self {
            Unit::Def => 72.0,
            Unit::Mid => 86.0,
            Unit::Gk | Unit::Fwd => 92.0,
        }
    }

    /// `DEFENDING_MIN_X`: ...and never drop deeper than these lines out of possession.
    fn defending_min_x(self) -> f64 {
        match self {
            Unit::Def => 9.0,
            Unit::Mid => 16.0,
            Unit::Gk | Unit::Fwd => 24.0,
        }
    }
}

/// Python's `min(a, b)`: `a` unless `b < a`.
#[inline]
pub fn py_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

/// Python's `max(a, b)`: `a` unless `b > a`.
#[inline]
pub fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// `pitch.clamp`: keep a point `margin` metres inside the pitch.
#[inline]
pub fn clamp(p: XY, margin: f64) -> XY {
    (
        py_min(py_max(p.0, margin), PITCH_LENGTH - margin),
        py_min(py_max(p.1, margin), PITCH_WIDTH - margin),
    )
}

/// `geometry.mirror`: a team frame to the opponent's frame (and back).
#[inline]
pub fn mirror(p: XY) -> XY {
    (PITCH_LENGTH - p.0, PITCH_WIDTH - p.1)
}

/// `pitch.team_shape`: where each player of a team stands, in that team's frame, given the ball (also
/// team frame). Writes one point per slot into `out` (cleared first) so the per-tick loop can reuse
/// the buffer.
pub fn team_shape(
    anchors: &[XY],
    units: &[Unit],
    ball: XY,
    in_possession: bool,
    press: f64,
    width: f64,
    out: &mut Vec<XY>,
) {
    let (bx, by) = ball;
    out.clear();
    for (&(ax, ay), &unit) in anchors.iter().zip(units) {
        if unit == Unit::Gk {
            let x = 5.0 + 0.12 * (bx - CENTRE.0);
            out.push(clamp(
                (
                    py_min(py_max(x, 2.0), 20.0),
                    CENTRE.1 + 0.15 * (by - CENTRE.1),
                ),
                0.5,
            ));
            continue;
        }
        let (x, y);
        if in_possession {
            let x0 = ax + 0.55 * (bx - CENTRE.0) + 6.0;
            x = py_min(x0, unit.possession_max_x());
            y = CENTRE.1 + (ay - CENTRE.1) * (0.85 + 0.3 * width) + 0.2 * (by - CENTRE.1);
        } else {
            let x0 = ax * 0.85 + 0.5 * (bx - CENTRE.0) + 8.0 * press - 3.0;
            x = py_max(x0, unit.defending_min_x());
            y = CENTRE.1 + (ay - CENTRE.1) * 0.75 + 0.3 * (by - CENTRE.1);
        }
        out.push(clamp((x, y), 1.0));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clamp_and_mirror() {
        assert_eq!(clamp((-3.0, 70.0), 0.5), (0.5, 67.5));
        assert_eq!(clamp((50.0, 30.0), 1.0), (50.0, 30.0));
        assert_eq!(mirror((5.0, 10.0)), (100.0, 58.0));
        assert_eq!(mirror(mirror((12.25, 3.5))), (12.25, 3.5));
    }

    #[test]
    fn python_min_max_tie_breaking() {
        assert_eq!(py_min(0.0, -0.0).to_bits(), 0.0f64.to_bits());
        assert_eq!(py_max(-0.0, 0.0).to_bits(), (-0.0f64).to_bits());
        assert_eq!(py_min(2.0, 1.0), 1.0);
        assert_eq!(py_max(2.0, 1.0), 2.0);
    }
}
