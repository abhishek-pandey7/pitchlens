//! Load generator for the PitchLens live stream (plan §5.3).
//!
//! Opens N SSE connections spread across M profiles against the edge (or the engine directly), and
//! reports delivery delay from the engine's `sent_at` on `state` messages (p50/p95/p99), messages per
//! second, and viewers that were dropped or resynced.
//!
//! ```text
//! loadgen --url http://127.0.0.1:8771/api/v1 --match SYN-NHR-SVC-7 --viewers 500 \
//!         --profiles raw,p-casual-es,p-analyst-en --frames true --events false \
//!         --ramp 10 --duration 60 [--engine http://127.0.0.1:8770/api/v1 --replay 10] [--json out.json]
//! ```
//!
//! With `--engine` and `--replay`, the replay is (re)started once every viewer is connected, so the
//! measured window is a live match. Clocks: `sent_at` is the engine's wall clock, so run the generator on
//! the same machine as the engine (or on clock-synchronised machines).

use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering::Relaxed};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use futures_util::StreamExt;
use pitchlens_edge::sse::Parser;
use pitchlens_edge::stats::{quantile_ms, Histogram};
use serde::Deserialize;
use serde_json::json;

struct Args {
    url: String,
    match_id: String,
    viewers: usize,
    profiles: Vec<Option<String>>,
    frames: bool,
    events: bool,
    ramp: Duration,
    duration: Duration,
    engine: Option<String>,
    replay: Option<f64>,
    from_ms: u64,
    json: Option<String>,
}

fn usage() -> ! {
    eprintln!(
        "usage: loadgen --url <stream base, e.g. http://127.0.0.1:8771/api/v1> --match <id> [--viewers 100]\n\
         \x20              [--profiles raw,p-casual-es] [--frames true] [--events false] [--ramp 10] [--duration 60]\n\
         \x20              [--engine <engine api base> --replay <speed> [--from-ms 0]] [--json <file>]"
    );
    std::process::exit(2)
}

fn parse_args() -> Args {
    let mut a = Args {
        url: String::new(),
        match_id: String::new(),
        viewers: 100,
        profiles: vec![None],
        frames: true,
        events: false,
        ramp: Duration::from_secs(10),
        duration: Duration::from_secs(60),
        engine: None,
        replay: None,
        from_ms: 0,
        json: None,
    };
    let mut it = std::env::args().skip(1);
    while let Some(flag) = it.next() {
        let mut value = || it.next().unwrap_or_else(|| usage());
        match flag.as_str() {
            "--url" => a.url = value().trim_end_matches('/').to_string(),
            "--match" => a.match_id = value(),
            "--viewers" => a.viewers = value().parse().unwrap_or_else(|_| usage()),
            "--profiles" => {
                a.profiles = value()
                    .split(',')
                    .map(str::trim)
                    .filter(|p| !p.is_empty())
                    .map(|p| (p != "raw").then(|| p.to_string()))
                    .collect()
            }
            "--frames" => a.frames = value() == "true",
            "--events" => a.events = value() == "true",
            "--ramp" => {
                a.ramp = Duration::from_secs_f64(value().parse().unwrap_or_else(|_| usage()))
            }
            "--duration" => {
                a.duration = Duration::from_secs_f64(value().parse().unwrap_or_else(|_| usage()))
            }
            "--engine" => a.engine = Some(value().trim_end_matches('/').to_string()),
            "--replay" => a.replay = Some(value().parse().unwrap_or_else(|_| usage())),
            "--from-ms" => a.from_ms = value().parse().unwrap_or_else(|_| usage()),
            "--json" => a.json = Some(value()),
            _ => usage(),
        }
    }
    if a.url.is_empty() || a.match_id.is_empty() || a.profiles.is_empty() || a.viewers == 0 {
        usage();
    }
    a
}

#[derive(Default)]
struct Totals {
    connected: AtomicUsize,
    failed: AtomicUsize,
    alive: AtomicUsize,
    dropped: AtomicUsize,
    resynced_viewers: AtomicUsize,
    resyncs: AtomicU64,
    out_of_order: AtomicU64,
    messages: AtomicU64,
    bytes: AtomicU64,
    states: AtomicU64,
    frames: AtomicU64,
    max_delay_ms: AtomicU64,
    delay: Histogram,
    /// Only messages after the ramp count towards rates and delays.
    measuring: std::sync::atomic::AtomicBool,
}

#[derive(Deserialize)]
struct State {
    sent_at: Option<u64>,
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

async fn viewer(client: reqwest::Client, url: String, totals: Arc<Totals>) {
    let resp = match client
        .get(&url)
        .header("accept", "text/event-stream")
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => r,
        Ok(r) => {
            eprintln!("connect: {} {}", r.status(), url);
            totals.failed.fetch_add(1, Relaxed);
            return;
        }
        Err(e) => {
            eprintln!("connect: {e}");
            totals.failed.fetch_add(1, Relaxed);
            return;
        }
    };
    totals.connected.fetch_add(1, Relaxed);
    totals.alive.fetch_add(1, Relaxed);
    let mut body = resp.bytes_stream();
    let mut parser = Parser::new();
    let mut syncs = 0u64;
    let mut last_id = 0u64;
    let mut resynced = false;
    while let Some(chunk) = body.next().await {
        let Ok(chunk) = chunk else { break };
        let measuring = totals.measuring.load(Relaxed);
        if measuring {
            totals.bytes.fetch_add(chunk.len() as u64, Relaxed);
        }
        for msg in parser.feed(&chunk) {
            if let Some(id) = msg.id {
                if id <= last_id {
                    totals.out_of_order.fetch_add(1, Relaxed);
                }
                last_id = id;
            }
            if msg.event == "sync" && msg.id.is_none() {
                syncs += 1;
                if syncs > 1 {
                    totals.resyncs.fetch_add(1, Relaxed);
                    if !resynced {
                        resynced = true;
                        totals.resynced_viewers.fetch_add(1, Relaxed);
                    }
                }
            }
            if !measuring {
                continue;
            }
            totals.messages.fetch_add(1, Relaxed);
            match msg.event.as_str() {
                "frame" => {
                    totals.frames.fetch_add(1, Relaxed);
                }
                "state" => {
                    totals.states.fetch_add(1, Relaxed);
                    if let Ok(State {
                        sent_at: Some(sent),
                    }) = serde_json::from_str::<State>(&msg.data)
                    {
                        let delay = now_ms().saturating_sub(sent);
                        totals.delay.record(Duration::from_millis(delay));
                        totals.max_delay_ms.fetch_max(delay, Relaxed);
                    }
                }
                _ => {}
            }
        }
    }
    totals.alive.fetch_sub(1, Relaxed);
    totals.dropped.fetch_add(1, Relaxed);
}

#[tokio::main]
async fn main() {
    let args = parse_args();
    let client = reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(10))
        .tcp_nodelay(true)
        .build()
        .expect("HTTP client");
    let totals = Arc::new(Totals::default());
    let mut tasks = Vec::with_capacity(args.viewers);
    let gap = args.ramp.div_f64(args.viewers as f64);
    let started = Instant::now();
    eprintln!(
        "opening {} viewers across {} profiles over {:?} (frames={}, events={})",
        args.viewers,
        args.profiles.len(),
        args.ramp,
        args.frames,
        args.events
    );
    for i in 0..args.viewers {
        let profile = &args.profiles[i % args.profiles.len()];
        let mut url = format!(
            "{}/matches/{}/stream?frames={}&events={}",
            args.url, args.match_id, args.frames, args.events
        );
        if let Some(p) = profile {
            url.push_str(&format!("&profile_id={p}"));
        }
        tasks.push(tokio::spawn(viewer(client.clone(), url, totals.clone())));
        tokio::time::sleep_until((started + gap * (i as u32 + 1)).into()).await;
    }
    // Let the last connections finish their handshake and snapshot.
    let settle = Instant::now() + Duration::from_secs(10);
    while totals.connected.load(Relaxed) + totals.failed.load(Relaxed) < args.viewers
        && Instant::now() < settle
    {
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    let ramp_took = started.elapsed();
    if let (Some(engine), Some(speed)) = (&args.engine, args.replay) {
        let r = client
            .post(format!("{engine}/matches/{}/replay", args.match_id))
            .json(&json!({"speed": speed, "from_ms": args.from_ms, "frames": args.frames}))
            .send()
            .await;
        match r {
            Ok(r) => eprintln!("replay at {speed}x: {}", r.status()),
            Err(e) => eprintln!("replay: {e}"),
        }
        tokio::time::sleep(Duration::from_secs(2)).await; // the replay's seek and first sync
    }
    eprintln!(
        "{} connected, {} failed in {:.1}s; measuring for {:?}",
        totals.connected.load(Relaxed),
        totals.failed.load(Relaxed),
        ramp_took.as_secs_f64(),
        args.duration
    );
    let resyncs_before = totals.resyncs.load(Relaxed);
    let dropped_before = totals.dropped.load(Relaxed);
    totals.measuring.store(true, Relaxed);
    let window = Instant::now();
    let mut tick = tokio::time::interval(Duration::from_secs(10));
    tick.tick().await;
    while window.elapsed() < args.duration {
        tick.tick().await;
        let delay = totals.delay.snapshot();
        eprintln!(
            "  t={:>4.0}s alive={} msgs/s={:.0} p95={:?}ms resyncs={} dropped={}",
            window.elapsed().as_secs_f64(),
            totals.alive.load(Relaxed),
            totals.messages.load(Relaxed) as f64 / window.elapsed().as_secs_f64(),
            quantile_ms(&delay, 0.95).map(|v| v.round()),
            totals.resyncs.load(Relaxed),
            totals.dropped.load(Relaxed),
        );
    }
    totals.measuring.store(false, Relaxed);
    let secs = window.elapsed().as_secs_f64();
    for t in &tasks {
        t.abort();
    }
    let delay = totals.delay.snapshot();
    let round = |v: Option<f64>| v.map(|v| (v * 10.0).round() / 10.0);
    let messages = totals.messages.load(Relaxed);
    let report = json!({
        "url": args.url,
        "match_id": args.match_id,
        "viewers": args.viewers,
        "profiles": args.profiles.len(),
        "frames": args.frames,
        "events": args.events,
        "replay_speed": args.replay,
        "connected": totals.connected.load(Relaxed),
        "failed": totals.failed.load(Relaxed),
        "measured_s": (secs * 10.0).round() / 10.0,
        "delivery_delay_ms": {
            "p50": round(quantile_ms(&delay, 0.50)),
            "p95": round(quantile_ms(&delay, 0.95)),
            "p99": round(quantile_ms(&delay, 0.99)),
            "max": totals.max_delay_ms.load(Relaxed),
            "samples": delay.iter().sum::<u64>(),
        },
        "messages_per_s": (messages as f64 / secs).round(),
        "messages_per_viewer_per_s": ((messages as f64 / secs / args.viewers as f64) * 10.0).round() / 10.0,
        "mbit_per_s": ((totals.bytes.load(Relaxed) as f64 * 8.0 / secs / 1e6) * 10.0).round() / 10.0,
        "state_messages": totals.states.load(Relaxed),
        "frame_messages": totals.frames.load(Relaxed),
        "dropped_viewers": totals.dropped.load(Relaxed),
        "dropped_during_window": totals.dropped.load(Relaxed) - dropped_before,
        "resynced_viewers": totals.resynced_viewers.load(Relaxed),
        "resyncs": totals.resyncs.load(Relaxed),
        "resyncs_during_window": totals.resyncs.load(Relaxed) - resyncs_before,
        "out_of_order_ids": totals.out_of_order.load(Relaxed),
    });
    let text = serde_json::to_string_pretty(&report).unwrap();
    println!("{text}");
    if let Some(path) = &args.json {
        if let Err(e) = std::fs::write(path, text + "\n") {
            eprintln!("write {path}: {e}");
        }
    }
}
