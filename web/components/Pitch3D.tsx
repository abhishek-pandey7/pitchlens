"use client";

// The pitch in 3D, from the same 5 Hz tracking frames as the 2D view. A broadcast camera on the near
// touchline pans with the ball; players and ball ease towards each new frame every animation frame.
// Loaded only in the browser (three.js needs WebGL), through `next/dynamic` in PitchView.
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { drawPitch, LENGTH, WIDTH } from "@/components/PitchCanvas";
import type { MatchMeta, TrackingFrame } from "@/lib/types";

const EASE = 0.3;
const RUN_OFF = 6; // grass beyond the lines, in metres
const FOCUS = "#3ccf8a";

type Props = {
  frame: TrackingFrame | null;
  meta: MatchMeta | null;
  focusPlayerId?: string | null;
  /** "follow" pans with the ball like a main broadcast camera; "wide" holds the whole pitch. */
  camera?: "follow" | "wide";
  className?: string;
  label?: string;
};

type Kit = { shirt: number; colors: [string, string] };
type Figure = { group: THREE.Group; ring: THREE.Mesh; x: number; y: number };

// Pitch coordinates (x 0..105 along the length, y 0..68 across, y = 0 on the far touchline) to the
// scene: x across the screen, z towards the camera, y up.
const sx = (x: number) => x - LENGTH / 2;
const sz = (y: number) => y - WIDTH / 2;


function grassTexture(): THREE.CanvasTexture {
  const scale = 20; // pixels per metre
  const canvas = document.createElement("canvas");
  canvas.width = (LENGTH + RUN_OFF * 2) * scale;
  canvas.height = (WIDTH + RUN_OFF * 2) * scale;
  const ctx = canvas.getContext("2d")!;
  ctx.fillStyle = "#1c5631";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.translate(RUN_OFF * scale, RUN_OFF * scale);
  drawPitch(ctx, scale);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 8;
  return texture;
}

function numberSprite(shirt: number, colors: [string, string]): THREE.Sprite {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 64;
  const ctx = canvas.getContext("2d")!;
  ctx.fillStyle = colors[0];
  ctx.beginPath();
  ctx.arc(32, 32, 28, 0, Math.PI * 2);
  ctx.fill();
  ctx.lineWidth = 4;
  ctx.strokeStyle = colors[1];
  ctx.stroke();
  ctx.fillStyle = colors[1];
  ctx.font = "700 30px Archivo, system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(String(shirt), 32, 34);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, depthTest: false }));
  sprite.scale.set(1.5, 1.5, 1);
  sprite.position.y = 2.9;
  sprite.renderOrder = 2;
  return sprite;
}

function blobShadow(radius: number, opacity: number): THREE.Mesh {
  const mesh = new THREE.Mesh(
    new THREE.CircleGeometry(radius, 24),
    new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity, depthWrite: false }),
  );
  mesh.rotation.x = -Math.PI / 2;
  mesh.position.y = 0.02;
  return mesh;
}

function goal(side: 0 | 1): THREE.Group {
  const group = new THREE.Group();
  const white = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.4 });
  const post = new THREE.CylinderGeometry(0.08, 0.08, 2.44, 12);
  for (const z of [-3.66, 3.66]) {
    const mesh = new THREE.Mesh(post, white);
    mesh.position.set(0, 1.22, z);
    group.add(mesh);
  }
  const bar = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.08, 7.32, 12), white);
  bar.rotation.x = Math.PI / 2;
  bar.position.y = 2.44;
  group.add(bar);
  const net = new THREE.Mesh(
    new THREE.BoxGeometry(2, 2.44, 7.32),
    new THREE.MeshBasicMaterial({ color: 0xffffff, wireframe: true, transparent: true, opacity: 0.18 }),
  );
  net.position.set(side ? 1 : -1, 1.22, 0);
  group.add(net);
  group.position.x = side ? LENGTH / 2 : -LENGTH / 2;
  return group;
}

// Pitch-side boards and stepped stands on the far side and behind both goals; the near side is where
// the camera stands.
function stadium(): THREE.Group {
  const group = new THREE.Group();
  const surround = new THREE.Mesh(
    new THREE.PlaneGeometry(LENGTH + 80, WIDTH + 70),
    new THREE.MeshStandardMaterial({ color: "#16422a", roughness: 1 }),
  );
  surround.rotation.x = -Math.PI / 2;
  surround.position.y = -0.02;
  group.add(surround);

  const board = new THREE.MeshStandardMaterial({ color: "#0b2545", roughness: 0.6 });
  const stripe = new THREE.MeshBasicMaterial({ color: "#3ccf8a" });
  const halfL = LENGTH / 2 + RUN_OFF;
  const halfW = WIDTH / 2 + RUN_OFF;
  const addBoard = (w: number, x: number, z: number, rotY: number) => {
    const b = new THREE.Mesh(new THREE.BoxGeometry(w, 0.9, 0.2), board);
    b.position.set(x, 0.45, z);
    b.rotation.y = rotY;
    const s = new THREE.Mesh(new THREE.BoxGeometry(w, 0.08, 0.22), stripe);
    s.position.set(x, 0.75, z);
    s.rotation.y = rotY;
    group.add(b, s);
  };
  addBoard(LENGTH + RUN_OFF * 2, 0, -halfW, 0);
  addBoard(WIDTH + RUN_OFF * 2, -halfL, 0, Math.PI / 2);
  addBoard(WIDTH + RUN_OFF * 2, halfL, 0, Math.PI / 2);

  const seats = [new THREE.MeshStandardMaterial({ color: "#2a3f61", roughness: 0.9 }), new THREE.MeshStandardMaterial({ color: "#33496d", roughness: 0.9 })];
  const tiers = 6;
  for (let i = 0; i < tiers; i++) {
    const rise = 1.6 * (i + 1);
    const back = 3 + i * 2.6;
    const far = new THREE.Mesh(new THREE.BoxGeometry(LENGTH + 40, rise, 2.6), seats[i % 2]);
    far.position.set(0, rise / 2, -(halfW + back));
    group.add(far);
    for (const side of [-1, 1]) {
      const end = new THREE.Mesh(new THREE.BoxGeometry(2.6, rise, WIDTH + 30), seats[i % 2]);
      end.position.set(side * (halfL + back), rise / 2, 0);
      group.add(end);
    }
  }
  const roof = new THREE.Mesh(
    new THREE.BoxGeometry(LENGTH + 44, 0.6, 14),
    new THREE.MeshStandardMaterial({ color: "#0b2545", roughness: 0.7 }),
  );
  roof.position.set(0, 13.5, -(halfW + 12));
  group.add(roof);
  return group;
}

function figure(kit: Kit | undefined): Figure {
  const colors = kit?.colors ?? ["#8a94a6", "#ffffff"];
  const group = new THREE.Group();
  const body = new THREE.Mesh(
    new THREE.CapsuleGeometry(0.42, 1.0, 6, 16),
    new THREE.MeshStandardMaterial({ color: colors[0], roughness: 0.55 }),
  );
  body.position.y = 0.92;
  group.add(body);
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(0.75, 0.95, 32),
    new THREE.MeshBasicMaterial({ color: FOCUS, transparent: true, opacity: 0.95, depthWrite: false }),
  );
  ring.rotation.x = -Math.PI / 2;
  ring.position.y = 0.03;
  ring.visible = false;
  group.add(ring);
  group.add(blobShadow(0.6, 0.28));
  if (kit) group.add(numberSprite(kit.shirt, kit.colors));
  return { group, ring, x: 0, y: 0 };
}

export function Pitch3D({ frame, meta, focusPlayerId, camera = "follow", className, label }: Props) {
  const mountRef = useRef<HTMLDivElement>(null);
  const targetRef = useRef<TrackingFrame | null>(frame);
  const focusRef = useRef(focusPlayerId);
  const cameraModeRef = useRef(camera);

  const kitsMemo = useMemo(() => {
    const map = new Map<string, Kit>();
    if (!meta) return map;
    for (const team of [meta.home, meta.away]) {
      for (const p of meta.lineups[team.club_id] ?? []) {
        map.set(p.player_id, { shirt: p.shirt, colors: [team.colors[0], team.colors[1]] });
      }
    }
    return map;
  }, [meta]);

  // Kits by player, read by the render loop: the scene is built once and outlives metadata updates.
  const kitsRef = useRef(kitsMemo);
  useEffect(() => {
    kitsRef.current = kitsMemo;
  }, [kitsMemo]);
  useEffect(() => {
    targetRef.current = frame;
  }, [frame]);
  useEffect(() => {
    focusRef.current = focusPlayerId;
  }, [focusPlayerId]);
  useEffect(() => {
    cameraModeRef.current = camera;
  }, [camera]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    mount.appendChild(renderer.domElement);
    renderer.domElement.style.display = "block";
    renderer.domElement.style.width = "100%";
    renderer.domElement.style.height = "100%";

    const scene = new THREE.Scene();
    scene.add(new THREE.HemisphereLight(0xeef4ff, 0x29452f, 1.5));
    const sun = new THREE.DirectionalLight(0xffffff, 1.6);
    sun.position.set(-30, 60, 40);
    scene.add(sun);

    const grass = grassTexture();
    const ground = new THREE.Mesh(
      new THREE.PlaneGeometry(LENGTH + RUN_OFF * 2, WIDTH + RUN_OFF * 2),
      new THREE.MeshStandardMaterial({ map: grass, roughness: 0.95 }),
    );
    ground.rotation.x = -Math.PI / 2;
    scene.add(ground);
    scene.add(goal(0), goal(1), stadium());
    scene.background = new THREE.Color("#dfe7f1");
    scene.fog = new THREE.Fog("#dfe7f1", 140, 260);

    const ball = new THREE.Mesh(
      new THREE.SphereGeometry(0.34, 24, 16),
      new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.35, emissive: 0x222222 }),
    );
    const ballShadow = blobShadow(0.4, 0.35);
    scene.add(ball, ballShadow);
    const ballPos = { x: LENGTH / 2, y: WIDTH / 2, z: 0 };

    const cam = new THREE.PerspectiveCamera(30, 16 / 9, 1, 400);
    const look = new THREE.Vector3(0, 0, 0);
    const figures = new Map<string, Figure>();
    let kits = kitsRef.current;

    const resize = () => {
      const { clientWidth: w, clientHeight: h } = mount;
      if (!w || !h) return;
      renderer.setSize(w, h, false);
      cam.aspect = w / h;
      cam.updateProjectionMatrix();
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(mount);

    let visible = true;
    const io = new IntersectionObserver(([entry]) => (visible = entry.isIntersecting));
    io.observe(mount);

    let raf = 0;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      if (!visible) return;
      if (kits !== kitsRef.current) {
        kits = kitsRef.current; // another match or new lineups: rebuild the figures with their kits
        for (const fig of figures.values()) scene.remove(fig.group);
        figures.clear();
      }
      const target = targetRef.current;
      if (target) {
        const present = new Set<string>();
        for (const p of target.players) {
          present.add(p.player_id);
          let fig = figures.get(p.player_id);
          if (!fig) {
            fig = figure(kits.get(p.player_id));
            fig.x = p.x;
            fig.y = p.y;
            figures.set(p.player_id, fig);
            scene.add(fig.group);
          }
          if (Math.hypot(fig.x - p.x, fig.y - p.y) > 15) {
            fig.x = p.x; // a seek: jump rather than glide across the pitch
            fig.y = p.y;
          } else {
            fig.x += (p.x - fig.x) * EASE;
            fig.y += (p.y - fig.y) * EASE;
          }
          fig.group.position.set(sx(fig.x), 0, sz(fig.y));
          const focused = p.player_id === focusRef.current;
          fig.ring.visible = focused;
          fig.group.scale.setScalar(focused ? 1.2 : 1);
        }
        for (const [id, fig] of figures) {
          if (present.has(id)) continue;
          scene.remove(fig.group);
          figures.delete(id);
        }
        ballPos.x += (target.ball.x - ballPos.x) * EASE * 1.5;
        ballPos.y += (target.ball.y - ballPos.y) * EASE * 1.5;
        ballPos.z += (target.ball.z - ballPos.z) * EASE * 1.5;
      }
      ball.position.set(sx(ballPos.x), 0.34 + Math.max(0, ballPos.z), sz(ballPos.y));
      ballShadow.position.set(sx(ballPos.x), 0.02, sz(ballPos.y));
      (ballShadow.material as THREE.MeshBasicMaterial).opacity = 0.35 / (1 + Math.max(0, ballPos.z) * 0.3);

      // Main camera: high on the near touchline. "follow" pans along the length with the ball.
      const follow = cameraModeRef.current === "follow" && !reduceMotion;
      const wantX = follow ? THREE.MathUtils.clamp(sx(ballPos.x) * 0.75, -30, 30) : 0;
      look.x += (wantX - look.x) * (follow ? 0.04 : 1);
      look.z = follow ? 2 : 0;
      if (follow) cam.position.set(look.x * 0.9, 38, 74);
      else cam.position.set(0, 72, 92);
      cam.lookAt(look);
      renderer.render(scene, cam);
    };
    raf = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
      io.disconnect();
      scene.traverse((obj) => {
        const mesh = obj as THREE.Mesh;
        mesh.geometry?.dispose();
        const material = mesh.material as THREE.Material | THREE.Material[] | undefined;
        for (const m of Array.isArray(material) ? material : material ? [material] : []) {
          (m as THREE.MeshBasicMaterial).map?.dispose();
          m.dispose();
        }
      });
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, []);

  return (
    <div
      ref={mountRef}
      role="img"
      aria-label={label ?? "Live 3D view of the pitch"}
      className={`aspect-video w-full overflow-hidden rounded-lg bg-[#dfe7f1] ${className ?? ""}`}
    />
  );
}
