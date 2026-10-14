//! Tracking frames as the JSON text the engine sends (port of `Tracking.frame` + Pydantic + `json.dumps`).
//!
//! Each frame becomes exactly the string Python produces with
//! `json.dumps(tracking.frame(i).model_dump(mode="json"), separators=(",", ":"), ensure_ascii=False)`:
//! the payload of an SSE `frame` message and one element of a goal clip. Returning the finished text
//! (rather than dicts) removes the Pydantic models, the `model_dump` and the `json.dumps` per frame.
//!
//! Coordinates are rounded like Python's `round(float(v), 2)` on the float32 value widened to f64, then
//! written like `json.dumps` writes a float (`repr`, with `NaN` / `Infinity` for non-finite values).
//! Python rounds the exact binary value to two decimals (ties to even) and parses that back; Rust's
//! `{:.2}` formats the exact binary value the same way. Below 1e12 the two-decimal text with one trailing
//! zero dropped is already the `repr` of the rounded float (doubles there are far closer together than
//! 0.01, so no shorter text reads back as the same float); larger values take the general path. Below
//! 1e6 the hundredths come from integer arithmetic unless the value is within a hair of a half-hundredth,
//! which goes through `{:.2}` instead.

use std::fmt::Write;

use ndarray::{ArrayView1, ArrayView2, ArrayView3};

// Point3 bounds in `schema/common.py`: the Python path rejects a ball outside them.
const PITCH_LENGTH: f64 = 105.0;
const PITCH_WIDTH: f64 = 68.0;
// Below this, the two-decimal text is the shortest round-trip text (see the module docs).
const FAST_ABS: f64 = 1e12;
// Below this, rounding goes through integer hundredths, without formatting and parsing a float.
const CENTS_ABS: f64 = 1e6;

fn w(out: &mut String, args: std::fmt::Arguments<'_>) {
    out.write_fmt(args)
        .expect("writing to a String cannot fail");
}

/// Append `json.dumps(round(v, 2))` and return the rounded value.
pub fn push_round2(out: &mut String, v: f64) -> f64 {
    if v.is_nan() {
        out.push_str("NaN");
        return v;
    }
    if v.is_infinite() {
        out.push_str(if v > 0.0 { "Infinity" } else { "-Infinity" });
        return v;
    }
    if v.abs() < CENTS_ABS {
        // Whole hundredths from v * 100, unless the product is within 1e-6 of a half: its rounding error
        // (< 1e-8 here) can't then change which way it rounds, so this is the exact value's rounding.
        let scaled = v * 100.0;
        let floor = scaled.floor();
        let frac = scaled - floor; // exact
        if (frac - 0.5).abs() > 1e-6 {
            let cents = (if frac > 0.5 { floor + 1.0 } else { floor }).abs() as u64;
            if v.is_sign_negative() {
                out.push('-'); // also -0.0 from a small negative value, as in Python
            }
            let (units, hundredths) = (cents / 100, cents % 100);
            if hundredths % 10 == 0 {
                w(out, format_args!("{units}.{}", hundredths / 10));
            } else {
                w(out, format_args!("{units}.{hundredths:02}"));
            }
            let rounded = cents as f64 / 100.0; // the double nearest the decimal, as Python parses it
            return if v.is_sign_negative() {
                -rounded
            } else {
                rounded
            };
        }
    }
    let start = out.len();
    w(out, format_args!("{v:.2}"));
    let rounded: f64 = out[start..].parse().expect("a formatted float parses");
    if v.abs() < FAST_ABS {
        if out.ends_with('0') {
            out.pop(); // "12.30" -> "12.3", "3.00" -> "3.0", "-0.00" -> "-0.0"
        }
        return rounded;
    }
    out.truncate(start);
    push_repr(out, rounded);
    rounded
}

/// Append Python's `repr` of a finite float: the shortest text that reads back as it (Rust's `{}` picks
/// the same digits), in exponent form from 1e16 up, written `1e+16` / `1.5e+16`.
fn push_repr(out: &mut String, v: f64) {
    if v.abs() >= 1e16 {
        let s = format!("{v:e}");
        let (mantissa, exp) = s.split_once('e').expect("{:e} has an exponent");
        let (sign, digits) = exp.strip_prefix('-').map_or(("+", exp), |d| ("-", d));
        w(out, format_args!("{mantissa}e{sign}{digits:0>2}"));
    } else {
        let start = out.len();
        w(out, format_args!("{v}"));
        if !out[start..].contains('.') {
            out.push_str(".0");
        }
    }
}

/// Append `s` as a JSON string the way `json.dumps(..., ensure_ascii=False)` writes it.
pub fn push_json_str(out: &mut String, s: &str) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => w(out, format_args!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

/// Why a frame can't be written: the arrays don't fit together, or Pydantic would reject the frame.
#[derive(Debug, PartialEq)]
pub enum FrameError {
    Shape(String),
    Value { frame: usize, what: String },
}

/// The arrays of a `Tracking`, borrowed.
pub struct Frames<'a> {
    pub match_id: &'a str,
    pub ms: ArrayView1<'a, i64>,
    pub ball: ArrayView2<'a, f32>,
    pub xy: ArrayView3<'a, f32>,
    pub player_ids: &'a [String],
}

impl Frames<'_> {
    fn len(&self) -> usize {
        self.ms.len()
    }

    fn check(&self) -> Result<(), FrameError> {
        let t = self.len();
        let (bt, bc) = self.ball.dim();
        let (xt, n, xc) = self.xy.dim();
        if bt != t || bc != 3 {
            return Err(FrameError::Shape(format!(
                "ball has shape ({bt}, {bc}), expected ({t}, 3)"
            )));
        }
        if xt != t || xc != 2 || n != self.player_ids.len() {
            return Err(FrameError::Shape(format!(
                "xy has shape ({xt}, {n}, {xc}), expected ({t}, {}, 2)",
                self.player_ids.len()
            )));
        }
        Ok(())
    }

    /// Frames `start..end`, clamped to the match like a Python slice of non-negative indices.
    pub fn render(&self, start: usize, end: usize) -> Result<Vec<String>, FrameError> {
        self.check()?;
        let end = end.min(self.len());
        let start = start.min(end);
        // Written once: every frame starts with the same match id, every player with the same id.
        let mut head = String::from("{\"match_id\":");
        push_json_str(&mut head, self.match_id);
        head.push_str(",\"match_ms\":");
        let ids: Vec<String> = self
            .player_ids
            .iter()
            .map(|pid| {
                let mut s = String::from("{\"player_id\":");
                push_json_str(&mut s, pid);
                s.push_str(",\"x\":");
                s
            })
            .collect();
        let mut out = Vec::with_capacity(end - start);
        for i in start..end {
            let mut s = String::with_capacity(head.len() + 64 + ids.len() * 48);
            self.render_one(i, &head, &ids, &mut s)?;
            out.push(s);
        }
        Ok(out)
    }

    fn render_one(
        &self,
        i: usize,
        head: &str,
        ids: &[String],
        s: &mut String,
    ) -> Result<(), FrameError> {
        let bad = |what: String| FrameError::Value { frame: i, what };
        let ms = self.ms[i];
        if ms < 0 {
            return Err(bad(format!("match_ms {ms} is negative")));
        }
        s.push_str(head);
        w(s, format_args!("{ms}"));
        s.push_str(",\"ball\":{");
        let bounds = [
            ("x", PITCH_LENGTH),
            ("y", PITCH_WIDTH),
            ("z", f64::INFINITY),
        ];
        for (k, (name, hi)) in bounds.into_iter().enumerate() {
            if k > 0 {
                s.push(',');
            }
            w(s, format_args!("\"{name}\":"));
            let v = f64::from(self.ball[[i, k]]);
            // Point3's ge/le checks run on the rounded value (NaN fails them, as in Pydantic).
            if !(0.0..=hi).contains(&push_round2(s, v)) {
                return Err(bad(format!("ball {name} = {v} is off the pitch")));
            }
        }
        s.push_str("},\"players\":[");
        let mut first = true;
        for (p, prefix) in ids.iter().enumerate() {
            let x = f64::from(self.xy[[i, p, 0]]);
            if x.is_nan() {
                continue; // off the pitch
            }
            if !first {
                s.push(',');
            }
            first = false;
            s.push_str(prefix);
            push_round2(s, x);
            s.push_str(",\"y\":");
            push_round2(s, f64::from(self.xy[[i, p, 1]]));
            s.push('}');
        }
        s.push_str("]}");
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ndarray::{Array1, Array2, Array3};

    fn r2(v: f64) -> String {
        let mut s = String::new();
        push_round2(&mut s, v);
        s
    }

    #[test]
    fn rounds_like_python() {
        // Expected values are CPython's json.dumps(round(v, 2)).
        assert_eq!(r2(12.3), "12.3");
        assert_eq!(r2(3.0), "3.0");
        assert_eq!(r2(0.0), "0.0");
        assert_eq!(r2(-0.0), "-0.0");
        assert_eq!(r2(-0.004), "-0.0");
        assert_eq!(r2(-1.5e-300), "-0.0");
        assert_eq!(r2(0.125), "0.12"); // an exact tie: half to even
        assert_eq!(r2(0.375), "0.38");
        assert_eq!(r2(2.675), "2.67"); // 2.67499999... in binary
        assert_eq!(r2(1.005), "1.0"); // 1.00499999...
        assert_eq!(r2(104.995), "105.0"); // 104.99500000000000455...
        assert_eq!(r2(-1.115), "-1.11");
        assert_eq!(r2(f64::from(0.1_f32)), "0.1");
        assert_eq!(r2(104.99999), "105.0");
        assert_eq!(r2(f64::NAN), "NaN");
        assert_eq!(r2(f64::NEG_INFINITY), "-Infinity");
        assert_eq!(r2(123456789012345.67), "123456789012345.67");
        assert_eq!(r2(1234567890123456.8), "1234567890123456.8");
        assert_eq!(r2(1e16), "1e+16");
        assert_eq!(r2(999999.995), "999999.99"); // 99999999.5 after * 100: the exact path
        assert_eq!(r2(-123.4), "-123.4");
        assert_eq!(r2(-0.01), "-0.01");
        assert_eq!(r2(0.07), "0.07");
        assert_eq!(r2(f64::from(f32::MAX)), "3.4028234663852886e+38");
    }

    #[test]
    fn escapes_like_json_dumps() {
        let mut s = String::new();
        push_json_str(&mut s, "a\"b\\c\nd\u{1}é\u{7f}");
        assert_eq!(s, "\"a\\\"b\\\\c\\nd\\u0001é\u{7f}\"");
    }

    #[test]
    fn renders_a_frame() {
        let ms = Array1::from(vec![0_i64, 200]);
        let ball =
            Array2::from_shape_vec((2, 3), vec![52.5_f32, 34.0, 0.0, 1.234, 0.006, 2.5]).unwrap();
        let mut xy = Array3::<f32>::from_elem((2, 2, 2), 10.0);
        xy[[1, 0, 0]] = f32::NAN;
        let ids = vec!["A-1".to_string(), "B-2".to_string()];
        let frames = Frames {
            match_id: "M",
            ms: ms.view(),
            ball: ball.view(),
            xy: xy.view(),
            player_ids: &ids,
        };
        let out = frames.render(0, 99).unwrap();
        assert_eq!(
            out[0],
            r#"{"match_id":"M","match_ms":0,"ball":{"x":52.5,"y":34.0,"z":0.0},"players":[{"player_id":"A-1","x":10.0,"y":10.0},{"player_id":"B-2","x":10.0,"y":10.0}]}"#
        );
        assert_eq!(
            out[1],
            r#"{"match_id":"M","match_ms":200,"ball":{"x":1.23,"y":0.01,"z":2.5},"players":[{"player_id":"B-2","x":10.0,"y":10.0}]}"#
        );
        assert!(frames.render(1, 1).unwrap().is_empty());
        assert!(frames.render(5, 9).unwrap().is_empty());
    }

    #[test]
    fn rejects_what_pydantic_rejects() {
        let ms = Array1::from(vec![0_i64]);
        let ball = Array2::from_shape_vec((1, 3), vec![105.01_f32, 34.0, 0.0]).unwrap();
        let xy = Array3::<f32>::zeros((1, 0, 2));
        let frames = Frames {
            match_id: "M",
            ms: ms.view(),
            ball: ball.view(),
            xy: xy.view(),
            player_ids: &[],
        };
        assert!(matches!(
            frames.render(0, 1),
            Err(FrameError::Value { frame: 0, .. })
        ));
        let ids = vec!["A".to_string()];
        let short = Frames {
            player_ids: &ids,
            ..frames
        };
        assert!(matches!(short.render(0, 1), Err(FrameError::Shape(_))));
    }
}
