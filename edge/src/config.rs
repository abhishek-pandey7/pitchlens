//! Configuration from the environment.

use std::time::Duration;

#[derive(Debug, Clone)]
pub struct Config {
    /// The engine's origin, e.g. `http://localhost:8000` (`ENGINE_URL`). Paths are appended to it.
    pub engine_url: String,
    /// Listening port (`PORT`, default 8080).
    pub port: u16,
    /// Allowed browser origins (`CORS_ORIGINS`, comma separated, like the engine; `*` allows any).
    pub cors_origins: Vec<String>,
    /// How long a channel's upstream stays open after its last viewer leaves (`CHANNEL_IDLE_SECS`).
    pub idle_close: Duration,
    /// Messages with an id kept per channel for `Last-Event-ID` resumes (`RING_SIZE`).
    pub ring_size: usize,
    /// Messages a viewer may fall behind the broadcast before it is resynced (`BROADCAST_CAPACITY`).
    pub broadcast_capacity: usize,
    /// Comment pings to idle viewers, as the engine does (`PING_SECS`).
    pub ping: Duration,
    /// A snapshot younger than this is shared by viewers joining together (`SNAPSHOT_TTL_MS`).
    pub snapshot_ttl: Duration,
    /// The upstream is reconnected when nothing (not even a ping) arrives for this long.
    pub upstream_idle_timeout: Duration,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            engine_url: "http://localhost:8000".into(),
            port: 8080,
            cors_origins: vec!["http://localhost:3000".into()],
            idle_close: Duration::from_secs(30),
            ring_size: 2000,
            broadcast_capacity: 1024,
            ping: Duration::from_secs(15),
            snapshot_ttl: Duration::from_millis(1000),
            upstream_idle_timeout: Duration::from_secs(45),
        }
    }
}

pub fn parse_list(value: &str) -> Vec<String> {
    value
        .split(',')
        .map(str::trim)
        .filter(|v| !v.is_empty())
        .map(String::from)
        .collect()
}

impl Config {
    pub fn from_env() -> Result<Self, String> {
        let d = Self::default();
        let var = |name: &str| std::env::var(name).ok().filter(|v| !v.trim().is_empty());
        fn num<T: std::str::FromStr>(
            name: &str,
            value: Option<String>,
            default: T,
        ) -> Result<T, String> {
            match value {
                Some(v) => v
                    .trim()
                    .parse()
                    .map_err(|_| format!("{name}: not a number: {v:?}")),
                None => Ok(default),
            }
        }
        Ok(Self {
            engine_url: var("ENGINE_URL")
                .unwrap_or(d.engine_url)
                .trim_end_matches('/')
                .to_string(),
            port: num("PORT", var("PORT"), d.port)?,
            cors_origins: var("CORS_ORIGINS")
                .map(|v| parse_list(&v))
                .unwrap_or(d.cors_origins),
            idle_close: Duration::from_secs(num(
                "CHANNEL_IDLE_SECS",
                var("CHANNEL_IDLE_SECS"),
                30,
            )?),
            ring_size: num("RING_SIZE", var("RING_SIZE"), d.ring_size)?.max(1),
            broadcast_capacity: num(
                "BROADCAST_CAPACITY",
                var("BROADCAST_CAPACITY"),
                d.broadcast_capacity,
            )?
            .max(16),
            ping: Duration::from_secs(num("PING_SECS", var("PING_SECS"), 15)?.max(1)),
            snapshot_ttl: Duration::from_millis(num(
                "SNAPSHOT_TTL_MS",
                var("SNAPSHOT_TTL_MS"),
                1000,
            )?),
            upstream_idle_timeout: d.upstream_idle_timeout,
        })
    }
}
