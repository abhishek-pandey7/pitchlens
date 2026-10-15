//! Server-Sent Events: an incremental parser for the upstream stream and the encoder for viewers.

use bytes::{Bytes, BytesMut};

/// One SSE message as the engine sent it. `id` is `None` for messages without an id (tracking frames,
/// the `sync` that opens a fresh connection).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Event {
    pub id: Option<u64>,
    pub event: String,
    pub data: String,
}

impl Event {
    /// The message encoded once, ready to be written to every viewer.
    pub fn encode(&self) -> Bytes {
        encode(self.id, &self.event, &self.data)
    }
}

/// Encode one SSE message. Multi-line data becomes one `data:` line per line, as the spec requires.
pub fn encode(id: Option<u64>, event: &str, data: &str) -> Bytes {
    let mut out = BytesMut::with_capacity(data.len() + event.len() + 32);
    if let Some(id) = id {
        out.extend_from_slice(b"id: ");
        out.extend_from_slice(id.to_string().as_bytes());
        out.extend_from_slice(b"\n");
    }
    out.extend_from_slice(b"event: ");
    out.extend_from_slice(event.as_bytes());
    out.extend_from_slice(b"\n");
    for line in data.split('\n') {
        out.extend_from_slice(b"data: ");
        out.extend_from_slice(line.strip_suffix('\r').unwrap_or(line).as_bytes());
        out.extend_from_slice(b"\n");
    }
    out.extend_from_slice(b"\n");
    out.freeze()
}

/// A comment line: keeps idle connections (and proxies) from timing out.
pub fn ping() -> Bytes {
    Bytes::from_static(b": ping\n\n")
}

/// Incremental parser: feed it chunks as they arrive, get complete messages back.
///
/// Lines end in `\n` or `\r\n` (what every server we talk to writes). Comments (pings) and `retry:` are
/// ignored. An `id:` that is not a number is ignored, like the engine ignores a non-numeric
/// `Last-Event-ID`.
#[derive(Default)]
pub struct Parser {
    buf: Vec<u8>,
    id: Option<u64>,
    event: Option<String>,
    data: Option<String>,
}

impl Parser {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn feed(&mut self, chunk: &[u8]) -> Vec<Event> {
        self.buf.extend_from_slice(chunk);
        let mut out = Vec::new();
        let mut start = 0;
        while let Some(pos) = self.buf[start..].iter().position(|&b| b == b'\n') {
            let end = start + pos;
            let mut line = &self.buf[start..end];
            if let Some(stripped) = line.strip_suffix(b"\r") {
                line = stripped;
            }
            let line = String::from_utf8_lossy(line).into_owned();
            if let Some(event) = self.line(&line) {
                out.push(event);
            }
            start = end + 1;
        }
        self.buf.drain(..start);
        out
    }

    fn line(&mut self, line: &str) -> Option<Event> {
        if line.is_empty() {
            let data = self.data.take();
            let event = self.event.take();
            let id = self.id.take();
            if data.is_none() && event.is_none() {
                return None;
            }
            return Some(Event {
                id,
                event: event.unwrap_or_else(|| "message".to_string()),
                data: data.unwrap_or_default(),
            });
        }
        if line.starts_with(':') {
            return None;
        }
        let (field, value) = match line.split_once(':') {
            Some((f, v)) => (f, v.strip_prefix(' ').unwrap_or(v)),
            None => (line, ""),
        };
        match field {
            "event" => self.event = Some(value.to_string()),
            "data" => match &mut self.data {
                Some(data) => {
                    data.push('\n');
                    data.push_str(value);
                }
                None => self.data = Some(value.to_string()),
            },
            "id" => self.id = value.parse().ok(),
            _ => {}
        }
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_split_chunks_crlf_comments_and_multiline_data() {
        let mut p = Parser::new();
        let mut got = p.feed(b": ping - 2026\r\n\r\nid: 7\r\nevent: state\r\nda");
        assert!(got.is_empty());
        got.extend(p.feed(b"ta: {\"a\":1}\r\n\r\nevent: frame\ndata: x\ndata: y\n\n"));
        assert_eq!(
            got,
            vec![
                Event {
                    id: Some(7),
                    event: "state".into(),
                    data: "{\"a\":1}".into()
                },
                Event {
                    id: None,
                    event: "frame".into(),
                    data: "x\ny".into()
                },
            ]
        );
    }

    #[test]
    fn encode_round_trips() {
        let e = Event {
            id: Some(12),
            event: "insight".into(),
            data: "a\nb".into(),
        };
        let bytes = e.encode();
        assert_eq!(&bytes[..], b"id: 12\nevent: insight\ndata: a\ndata: b\n\n");
        assert_eq!(Parser::new().feed(&bytes), vec![e]);
    }
}
