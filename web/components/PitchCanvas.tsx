"use client";

// 2D pitch rendered from 5 Hz tracking frames. Positions ease towards each new frame every animation
// frame, so movement looks continuous even when frames are thinned at high replay speeds.
import { useEffect, useMemo, useRef } from "react";
import type { MatchMeta, TrackingFrame } from "@/lib/types";

const LENGTH = 105;
const WIDTH = 68;
const EASE = 0.3;

type Props = {
  frame: TrackingFrame | null;
  meta: MatchMeta | null;
  focusPlayerId?: string | null;
  className?: string;
};

type Dot = { x: number; y: number };

function drawPitch(ctx: CanvasRenderingContext2D, s: number) {
  const stripes = 12;
  for (let i = 0; i < stripes; i++) {
    ctx.fillStyle = i % 2 ? "#1d5a33" : "#20623a";
    ctx.fillRect((i * LENGTH * s) / stripes, 0, (LENGTH * s) / stripes + 1, WIDTH * s);
  }
  ctx.strokeStyle = "rgba(255,255,255,0.85)";
  ctx.lineWidth = Math.max(1, s * 0.15);
  const rect = (x: number, y: number, w: number, h: number) => ctx.strokeRect(x * s, y * s, w * s, h * s);
  const circle = (x: number, y: number, r: number, a0 = 0, a1 = Math.PI * 2) => {
    ctx.beginPath();
    ctx.arc(x * s, y * s, r * s, a0, a1);
    ctx.stroke();
  };
  const dot = (x: number, y: number) => {
    ctx.beginPath();
    ctx.arc(x * s, y * s, Math.max(1.5, s * 0.3), 0, Math.PI * 2);
    ctx.fillStyle = "rgba(255,255,255,0.85)";
    ctx.fill();
  };
  rect(0, 0, LENGTH, WIDTH);
  ctx.beginPath();
  ctx.moveTo((LENGTH / 2) * s, 0);
  ctx.lineTo((LENGTH / 2) * s, WIDTH * s);
  ctx.stroke();
  circle(LENGTH / 2, WIDTH / 2, 9.15);
  dot(LENGTH / 2, WIDTH / 2);
  for (const side of [0, 1]) {
    const x0 = side ? LENGTH - 16.5 : 0;
    rect(x0, WIDTH / 2 - 20.16, 16.5, 40.32);
    rect(side ? LENGTH - 5.5 : 0, WIDTH / 2 - 9.16, 5.5, 18.32);
    const spot = side ? LENGTH - 11 : 11;
    dot(spot, WIDTH / 2);
    const arc = Math.acos(5.5 / 9.15);
    circle(spot, WIDTH / 2, 9.15, side ? Math.PI - arc : -arc, side ? Math.PI + arc : arc);
    ctx.strokeRect((side ? LENGTH : -2) * s, (WIDTH / 2 - 3.66) * s, 2 * s, 7.32 * s);
  }
}

export function PitchCanvas({ frame, meta, focusPlayerId, className }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const targetRef = useRef<TrackingFrame | null>(null);
  const shownRef = useRef<Map<string, Dot>>(new Map());
  const ballRef = useRef<Dot & { z: number }>({ x: LENGTH / 2, y: WIDTH / 2, z: 0 });

  const roster = useMemo(() => {
    const map = new Map<string, { club: string; shirt: number; colors: [string, string] }>();
    if (!meta) return map;
    for (const team of [meta.home, meta.away]) {
      for (const p of meta.lineups[team.club_id] ?? []) {
        map.set(p.player_id, { club: team.club_id, shirt: p.shirt, colors: [team.colors[0], team.colors[1]] });
      }
    }
    return map;
  }, [meta]);

  useEffect(() => {
    targetRef.current = frame;
  }, [frame]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let raf = 0;
    const render = () => {
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      const ratio = window.devicePixelRatio || 1;
      const cssWidth = canvas.clientWidth;
      const cssHeight = (cssWidth * WIDTH) / LENGTH;
      if (canvas.width !== Math.round(cssWidth * ratio)) {
        canvas.width = Math.round(cssWidth * ratio);
        canvas.height = Math.round(cssHeight * ratio);
      }
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      const s = cssWidth / LENGTH;
      drawPitch(ctx, s);

      const target = targetRef.current;
      if (target) {
        const shown = shownRef.current;
        const present = new Set<string>();
        for (const p of target.players) {
          present.add(p.player_id);
          const cur = shown.get(p.player_id);
          if (!cur || Math.hypot(cur.x - p.x, cur.y - p.y) > 15) shown.set(p.player_id, { x: p.x, y: p.y });
          else shown.set(p.player_id, { x: cur.x + (p.x - cur.x) * EASE, y: cur.y + (p.y - cur.y) * EASE });
        }
        for (const id of [...shown.keys()]) if (!present.has(id)) shown.delete(id);
        const ball = ballRef.current;
        ball.x += (target.ball.x - ball.x) * EASE * 1.5;
        ball.y += (target.ball.y - ball.y) * EASE * 1.5;
        ball.z = target.ball.z;

        const radius = Math.max(4, s * 1.25);
        for (const [id, pos] of shown) {
          const info = roster.get(id);
          const focus = id === focusPlayerId;
          ctx.beginPath();
          ctx.arc(pos.x * s, pos.y * s, focus ? radius * 1.35 : radius, 0, Math.PI * 2);
          ctx.fillStyle = info?.colors[0] ?? "#999";
          ctx.fill();
          ctx.lineWidth = focus ? 3 : 1.5;
          ctx.strokeStyle = focus ? "#38e1a6" : (info?.colors[1] ?? "#fff");
          ctx.stroke();
          if (info && s > 5) {
            ctx.fillStyle = info.colors[1];
            ctx.font = `600 ${Math.round(radius * 1.05)}px var(--font-geist-sans), sans-serif`;
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.fillText(String(info.shirt), pos.x * s, pos.y * s + 0.5);
          }
        }
        const lift = Math.min(ball.z, 12);
        ctx.beginPath();
        ctx.arc(ball.x * s + lift * 0.3 * s, ball.y * s + lift * 0.3 * s, radius * 0.45, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(0,0,0,0.35)";
        ctx.fill();
        ctx.beginPath();
        ctx.arc(ball.x * s, ball.y * s, radius * (0.55 + lift * 0.03), 0, Math.PI * 2);
        ctx.fillStyle = "#ffffff";
        ctx.fill();
      }
      raf = requestAnimationFrame(render);
    };
    raf = requestAnimationFrame(render);
    return () => cancelAnimationFrame(raf);
  }, [roster, focusPlayerId]);

  return (
    <canvas
      ref={canvasRef}
      role="img"
      aria-label="Live 2D view of the pitch"
      className={`block w-full rounded-xl shadow-lg ${className ?? ""}`}
      style={{ aspectRatio: `${LENGTH} / ${WIDTH}` }}
    />
  );
}
