"use client";

// Subscribes to a match's SSE stream. EventSource reconnects on its own and resends Last-Event-ID,
// which the engine uses to replay exactly the messages this fan missed.
//
// Messages are buffered and folded into the view once per animation frame: at high replay speeds the
// stream can deliver hundreds of messages a second, and rendering each one separately would leave the
// page minutes behind the live match.
import { useEffect, useReducer } from "react";
import { api } from "./api";
import { initialView, reduce, type MatchView, type StreamMessage } from "./stream";

const EVENTS = ["sync", "reset", "state", "event", "insight", "narration", "cue", "frame", "alert", "control"];

type Action =
  | { kind: "batch"; messages: StreamMessage[] }
  | { kind: "status"; status: MatchView["status"] }
  | { kind: "clear" };

function reducer(view: MatchView, action: Action): MatchView {
  switch (action.kind) {
    case "clear":
      return initialView;
    case "status":
      return view.status === "finished" && action.status === "reconnecting" ? view : { ...view, status: action.status };
    case "batch":
      return action.messages.reduce(reduce, view);
  }
}

export function useMatchStream(
  matchId: string | null,
  opts: { profileId?: string | null; frames?: boolean; events?: boolean } = {},
): MatchView {
  const [view, dispatch] = useReducer(reducer, initialView);
  const { profileId, frames, events = false } = opts;

  useEffect(() => {
    if (!matchId) return;
    dispatch({ kind: "clear" });
    let buffer: StreamMessage[] = [];
    let scheduled = 0;
    const flush = () => {
      scheduled = 0;
      const messages = buffer;
      buffer = [];
      if (messages.length) dispatch({ kind: "batch", messages });
    };
    const source = new EventSource(api.streamUrl(matchId, { profileId, frames, events }));
    const handlers = EVENTS.map((name) => {
      const handler = (e: MessageEvent<string>) => {
        const id = e.lastEventId ? Number(e.lastEventId) : null;
        buffer.push({ event: name, data: JSON.parse(e.data), id, at: Date.now() });
        if (!scheduled) scheduled = requestAnimationFrame(flush);
      };
      source.addEventListener(name, handler as EventListener);
      return [name, handler] as const;
    });
    source.onerror = () => dispatch({ kind: "status", status: "reconnecting" });
    // Expire cues even when the stream goes quiet (e.g. after full time).
    const ticker = setInterval(() => dispatch({ kind: "batch", messages: [{ event: "tick", data: null, at: Date.now() }] }), 1000);
    return () => {
      clearInterval(ticker);
      for (const [name, handler] of handlers) source.removeEventListener(name, handler as EventListener);
      if (scheduled) cancelAnimationFrame(scheduled);
      source.close();
    };
  }, [matchId, profileId, frames, events]);

  return view;
}
