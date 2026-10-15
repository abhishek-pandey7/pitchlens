//! Counters and a lock-free latency histogram for `/stats`.

use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering::Relaxed};
use std::sync::Mutex;
use std::time::{Duration, Instant};

/// Log-linear buckets over microseconds: 8 sub-buckets per power of two, up to about 70 minutes.
const SUB: usize = 8;
const BUCKETS: usize = 32 * SUB;

pub struct Histogram {
    buckets: Vec<AtomicU64>,
}

fn bucket(us: u64) -> usize {
    if us < SUB as u64 {
        return us as usize;
    }
    let exp = 63 - us.leading_zeros() as usize; // floor(log2)
    let shift = exp - 3; // SUB = 2^3
    let sub = ((us >> shift) as usize) & (SUB - 1);
    ((exp - 2) * SUB + sub).min(BUCKETS - 1)
}

fn bucket_floor(i: usize) -> u64 {
    if i < SUB {
        return i as u64;
    }
    let exp = i / SUB + 2;
    let sub = (i % SUB) as u64;
    (SUB as u64 + sub) << (exp - 3)
}

impl Default for Histogram {
    fn default() -> Self {
        Self {
            buckets: (0..BUCKETS).map(|_| AtomicU64::new(0)).collect(),
        }
    }
}

impl Histogram {
    pub fn record(&self, d: Duration) {
        self.buckets[bucket(d.as_micros() as u64)].fetch_add(1, Relaxed);
    }

    pub fn snapshot(&self) -> Vec<u64> {
        self.buckets.iter().map(|b| b.load(Relaxed)).collect()
    }

    pub fn clear(&self) {
        for b in &self.buckets {
            b.store(0, Relaxed);
        }
    }
}

/// Quantile (0..1) in milliseconds from bucket counts, and the sample count.
pub fn quantile_ms(counts: &[u64], q: f64) -> Option<f64> {
    let total: u64 = counts.iter().sum();
    if total == 0 {
        return None;
    }
    let rank = ((total as f64) * q).ceil().max(1.0) as u64;
    let mut seen = 0;
    for (i, &c) in counts.iter().enumerate() {
        seen += c;
        if seen >= rank {
            // Midpoint of the bucket: within ~6% of the true value.
            let lo = bucket_floor(i) as f64;
            let hi = if i + 1 < BUCKETS {
                bucket_floor(i + 1) as f64
            } else {
                lo
            };
            return Some((lo + hi) / 2.0 / 1000.0);
        }
    }
    None
}

#[derive(Default)]
pub struct Stats {
    pub messages_in: AtomicU64,
    pub messages_out: AtomicU64,
    pub bytes_out: AtomicU64,
    pub viewers: AtomicUsize,
    pub viewers_total: AtomicU64,
    pub resumes_local: AtomicU64,
    pub snapshot_joins: AtomicU64,
    pub snapshot_fetches: AtomicU64,
    pub resyncs_lagged: AtomicU64,
    pub resyncs_upstream: AtomicU64,
    pub upstream_connects: AtomicU64,
    /// Fan-out delay: upstream message parsed -> handed to a viewer's connection. Two windows, so the
    /// quantiles always cover between 10 and 20 seconds of traffic.
    pub delay: [Histogram; 2],
    window: AtomicUsize,
    pub rates: Mutex<Rates>,
}

#[derive(Default, Clone, Copy)]
pub struct Rates {
    pub in_per_s: f64,
    pub out_per_s: f64,
    last_in: u64,
    last_out: u64,
    last_at: Option<Instant>,
    window_started: Option<Instant>,
}

pub const DELAY_WINDOW: Duration = Duration::from_secs(10);

impl Stats {
    pub fn record_delay(&self, d: Duration) {
        self.delay[self.window.load(Relaxed) & 1].record(d);
    }

    /// Called about once a second: message rates, and the delay window rotation.
    pub fn tick(&self, now: Instant) {
        let mut r = self.rates.lock().unwrap();
        let msgs_in = self.messages_in.load(Relaxed);
        let msgs_out = self.messages_out.load(Relaxed);
        if let Some(last) = r.last_at {
            let dt = now.duration_since(last).as_secs_f64().max(1e-3);
            r.in_per_s = (msgs_in - r.last_in) as f64 / dt;
            r.out_per_s = (msgs_out - r.last_out) as f64 / dt;
        }
        r.last_in = msgs_in;
        r.last_out = msgs_out;
        r.last_at = Some(now);
        let started = *r.window_started.get_or_insert(now);
        if now.duration_since(started) >= DELAY_WINDOW {
            let next = (self.window.load(Relaxed) + 1) & 1;
            self.delay[next].clear(); // drop the older window, then write into it
            self.window.store(next, Relaxed);
            r.window_started = Some(now);
        }
    }

    pub fn delay_counts(&self) -> Vec<u64> {
        let a = self.delay[0].snapshot();
        let b = self.delay[1].snapshot();
        a.iter().zip(b).map(|(x, y)| x + y).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn buckets_are_monotonic_and_quantiles_close() {
        let mut last = 0;
        for us in [
            0u64, 1, 7, 8, 9, 15, 16, 100, 1000, 12_345, 1_000_000, 60_000_000,
        ] {
            let b = bucket(us);
            assert!(b >= last, "{us}");
            assert!(bucket_floor(b) <= us, "{us}: floor {}", bucket_floor(b));
            last = b;
        }
        let h = Histogram::default();
        for ms in 1..=100u64 {
            h.record(Duration::from_millis(ms));
        }
        let p50 = quantile_ms(&h.snapshot(), 0.5).unwrap();
        let p95 = quantile_ms(&h.snapshot(), 0.95).unwrap();
        assert!((p50 - 50.0).abs() < 4.0, "{p50}");
        assert!((p95 - 95.0).abs() < 7.0, "{p95}");
    }
}
