import { useEffect, useId, useRef, useState } from 'react';
import './AtlasGlobe.css';

type AtlasGlobeProps = { destination: number; reducedMotion: boolean };
type Vector = [number, number, number];
type LandAsset = { points: number[]; coasts: number[][] };
type SceneControl = { update: (destination: number, reducedMotion: boolean) => void };
type DragSample = { time: number; yaw: number; pitch: number };
type GlobeDrag = {
  pointerId: number;
  touch: boolean;
  committed: boolean;
  startX: number;
  startY: number;
  lastX: number;
  lastY: number;
  samples: DragSample[];
};

const DEG = Math.PI / 180;
const TAU = Math.PI * 2;
const ASSET_ROOT = `${import.meta.env.BASE_URL}assets/`;
const PLACES = [
  { name: 'SAN FRANCISCO', slug: 'san-francisco', latitude: 37.7749, longitude: -122.4194 },
  { name: 'TOKYO', slug: 'tokyo', latitude: 35.6762, longitude: 139.6503 },
  { name: 'PARIS', slug: 'paris', latitude: 48.8566, longitude: 2.3522 },
] as const;

const clamp = (value: number, min = 0, max = 1) => Math.min(max, Math.max(min, value));
const destinationIndex = (value: number) => Number.isFinite(value) ? clamp(Math.round(value), 0, 2) : 0;
const angleDifference = (value: number) => Math.atan2(Math.sin(value), Math.cos(value));

function geographic(longitude: number, latitude: number, radius = 1): Vector {
  const latitudeRadians = latitude * DEG;
  const longitudeRadians = longitude * DEG;
  return [
    radius * Math.cos(latitudeRadians) * Math.sin(longitudeRadians),
    radius * Math.sin(latitudeRadians),
    radius * Math.cos(latitudeRadians) * Math.cos(longitudeRadians),
  ];
}

function geographicArray(coordinates: number[]): Float32Array {
  const result = new Float32Array(coordinates.length / 2 * 3);
  for (let i = 0, j = 0; i < coordinates.length; i += 2, j += 3) {
    const point = geographic(coordinates[i] / 100, coordinates[i + 1] / 100);
    result[j] = point[0];
    result[j + 1] = point[1];
    result[j + 2] = point[2];
  }
  return result;
}

function makeGraticule(): Float32Array[] {
  const lines: Float32Array[] = [];
  for (let latitude = -75; latitude <= 75; latitude += 15) {
    const line: number[] = [];
    for (let longitude = -180; longitude <= 180; longitude += 2) {
      line.push(...geographic(longitude, latitude));
    }
    lines.push(new Float32Array(line));
  }
  for (let longitude = -180; longitude < 180; longitude += 15) {
    const line: number[] = [];
    for (let latitude = -90; latitude <= 90; latitude += 2) {
      line.push(...geographic(longitude, latitude));
    }
    lines.push(new Float32Array(line));
  }
  return lines;
}

// Great-circle interpolation, raised above the surface. These are actual world
// positions: the same rotation and sphere occlusion apply to the land and routes.
function makeRoute(destination: number): Float32Array {
  const origin = geographic(PLACES[0].longitude, PLACES[0].latitude);
  const place = PLACES[destination];
  const target = geographic(place.longitude, place.latitude);
  const omega = Math.acos(clamp(origin[0] * target[0] + origin[1] * target[1] + origin[2] * target[2], -1, 1));
  const denominator = Math.sin(omega);
  const route = new Float32Array(121 * 3);
  for (let i = 0; i <= 120; i++) {
    const t = i / 120;
    const a = Math.sin((1 - t) * omega) / denominator;
    const b = Math.sin(t * omega) / denominator;
    const height = 1.006 + Math.sin(Math.PI * t) * 0.23;
    for (let axis = 0; axis < 3; axis++) route[i * 3 + axis] = (a * origin[axis] + b * target[axis]) * height;
  }
  return route;
}

export default function AtlasGlobe({ destination, reducedMotion }: AtlasGlobeProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const controlRef = useRef<SceneControl | null>(null);
  const instructionId = useId();
  const [ready, setReady] = useState(false);
  const place = PLACES[destinationIndex(destination)];

  useEffect(() => {
    const host = hostRef.current;
    const canvas = canvasRef.current;
    if (!host || !canvas) return;
    let context: CanvasRenderingContext2D | null = null;
    try { context = canvas.getContext('2d', { alpha: true }); } catch { return; }
    if (!context) return;
    const ctx = context;
    const hero = document.getElementById('atlas-hero');
    const abort = new AbortController();
    const graticule = makeGraticule();
    const routes = [makeRoute(1), makeRoute(2)];
    const orbit = new Float32Array(181 * 3);
    for (let i = 0; i <= 180; i++) {
      const t = i / 180 * TAU;
      orbit[i * 3] = Math.cos(t) * 1.19;
      orbit[i * 3 + 1] = Math.sin(t) * 0.36;
      orbit[i * 3 + 2] = Math.sin(t) * 1.134;
    }

    let selected = destinationIndex(destination);
    let reduceMotion = reducedMotion;
    let width = 0;
    let height = 0;
    let pixelRatio = 1;
    let frame = 0;
    let lastTime = 0;
    let visible = true;
    let disposed = false;
    let contextLost = false;
    let painted = false;
    let loaded = false;
    let points: Float32Array = new Float32Array();
    let projected: Float32Array = new Float32Array();
    let coasts: Float32Array[] = [];
    const dotBuckets: number[][] = Array.from({ length: 8 }, () => []);
    let manualYaw = 0;
    let manualPitch = 0;
    let drag: GlobeDrag | null = null;
    let velocityYaw = 0;
    let velocityPitch = 0;
    let momentumTime = 0;
    let targetProgress = 0;
    let progress = 0;
    let yaw = (PLACES[selected].longitude + 18) * DEG;
    let pitch = 22 * DEG;
    let roll = -12 * DEG;
    let targetYaw = yaw;
    let targetPitch = pitch;
    let targetRoll = roll;

    // Matrix coefficients are shared by every dot, coastline, grid and arc.
    const matrix = new Float64Array(9);
    const point: Vector = [0, 0, 0];

    function project(x: number, y: number, z: number, output: Vector) {
      output[0] = matrix[0] * x + matrix[1] * y + matrix[2] * z;
      output[1] = matrix[3] * x + matrix[4] * y + matrix[5] * z;
      output[2] = matrix[6] * x + matrix[7] * y + matrix[8] * z;
    }

    function occluded(p: Vector) {
      const distance = p[0] * p[0] + p[1] * p[1];
      return distance < 1 && p[2] < Math.sqrt(1 - distance) - 0.002;
    }

    function updateTargets() {
      if (drag?.committed) return;
      const scroll = reduceMotion ? 0 : targetProgress;
      const longitude = (PLACES[selected].longitude + 18 + scroll * 52) * DEG;
      targetYaw = yaw + angleDifference(longitude + manualYaw - yaw);
      targetPitch = clamp((22 + Math.sin(scroll * Math.PI) * 8) * DEG + manualPitch, -78 * DEG, 78 * DEG);
      targetRoll = (-12 + scroll * 18) * DEG;
    }

    function rememberManualView() {
      const scroll = reduceMotion ? 0 : targetProgress;
      manualYaw = angleDifference(yaw - (PLACES[selected].longitude + 18 + scroll * 52) * DEG);
      manualPitch = pitch - (22 + Math.sin(scroll * Math.PI) * 8) * DEG;
      targetYaw = yaw;
      targetPitch = pitch;
      targetRoll = roll;
    }

    function stopMomentum() {
      velocityYaw = 0;
      velocityPitch = 0;
      momentumTime = 0;
    }

    function readScroll() {
      const section = hero ?? host!;
      const rect = section.getBoundingClientRect();
      const sectionRange = section.offsetHeight - window.innerHeight;
      const range = sectionRange > 0 ? sectionRange : window.innerHeight * 0.3;
      targetProgress = clamp(-rect.top / range);
      updateTargets();
      wake();
    }

    function resize() {
      cancelInteraction();
      const bounds = host!.getBoundingClientRect();
      width = bounds.width;
      height = bounds.height;
      pixelRatio = Math.min(window.devicePixelRatio || 1, 1.5);
      const canvasWidth = Math.round(width * pixelRatio);
      const canvasHeight = Math.round(height * pixelRatio);
      if (canvas!.width !== canvasWidth || canvas!.height !== canvasHeight) {
        canvas!.width = canvasWidth;
        canvas!.height = canvasHeight;
      }
      readScroll();
    }

    function stop() {
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
      lastTime = 0;
    }

    function wake() {
      if (frame || disposed || contextLost || !visible || document.hidden || !loaded || width <= 0) return;
      frame = requestAnimationFrame(drawFrame);
    }

    function render() {
      const a = Math.cos(yaw), b = Math.sin(yaw);
      const c = Math.cos(pitch), d = Math.sin(pitch);
      const e = Math.cos(roll), f = Math.sin(roll);
      matrix.set([e * a + f * d * b, -f * c, -e * b + f * d * a,
        f * a - e * d * b, e * c, -f * b - e * d * a,
        c * b, d, c * a]);
      const cx = width * 0.5;
      const cy = height * 0.5;
      // The largest route radius is 1.236. Fit that full action envelope, with
      // label/ring clearance, to the measured layout slot on every viewport.
      const radius = Math.min(width / 2.66, height / 2.66) * (1 + Math.sin(progress * Math.PI) * 0.015);
      const dotScale = clamp(radius / 244, 0.65, 1.35);
      ctx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
      ctx.clearRect(0, 0, width, height);

      const halo = ctx.createRadialGradient(cx, cy, radius * 0.91, cx, cy, radius * 1.28);
      halo.addColorStop(0, 'rgba(169,225,122,0.045)');
      halo.addColorStop(0.35, 'rgba(169,225,122,0.035)');
      halo.addColorStop(1, 'rgba(169,225,122,0)');
      ctx.fillStyle = halo;
      ctx.fillRect(cx - radius * 1.3, cy - radius * 1.3, radius * 2.6, radius * 2.6);

      const sea = ctx.createRadialGradient(cx - radius * 0.36, cy - radius * 0.43, 0, cx, cy, radius * 1.05);
      sea.addColorStop(0, '#1a251b');
      sea.addColorStop(0.4, '#111a13');
      sea.addColorStop(0.8, '#0b110d');
      sea.addColorStop(1, '#080a09');
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, TAU);
      ctx.fillStyle = sea;
      ctx.fill();
      ctx.strokeStyle = 'rgba(178,218,147,0.23)';
      ctx.lineWidth = 0.7;
      ctx.stroke();

      function surfacePaths(lines: Float32Array[], color: string, lineWidth: number) {
        ctx.beginPath();
        for (const line of lines) {
          let pen = false;
          for (let i = 0; i < line.length; i += 3) {
            project(line[i], line[i + 1], line[i + 2], point);
            if (point[2] <= 0.015) { pen = false; continue; }
            const x = cx + point[0] * radius, y = cy - point[1] * radius;
            if (pen) ctx.lineTo(x, y); else ctx.moveTo(x, y);
            pen = true;
          }
        }
        ctx.strokeStyle = color;
        ctx.lineWidth = lineWidth;
        ctx.stroke();
      }

      surfacePaths(graticule, 'rgba(185,215,155,0.12)', 0.55);

      for (const bucket of dotBuckets) bucket.length = 0;
      for (let i = 0; i < points.length; i += 3) {
        project(points[i], points[i + 1], points[i + 2], point);
        if (point[2] <= 0.02) continue;
        projected[i] = cx + point[0] * radius;
        projected[i + 1] = cy - point[1] * radius;
        projected[i + 2] = point[2];
        const light = clamp(-point[0] * 0.35 + point[1] * 0.48 + point[2] * 0.8);
        const bucket = Math.min(7, Math.floor((0.15 + point[2] * 0.52 + light * 0.32) * 8));
        dotBuckets[bucket].push(i);
      }
      // Surface depth buckets keep brighter near-side dots above the fine grid
      // and avoid thousands of style changes on each input-driven frame.
      for (let i = 0; i < dotBuckets.length; i++) {
        ctx.beginPath();
        for (const index of dotBuckets[i]) {
          const size = (0.43 + projected[index + 2] * 0.34) * dotScale;
          ctx.moveTo(projected[index] + size, projected[index + 1]);
          ctx.arc(projected[index], projected[index + 1], size, 0, TAU);
        }
        ctx.fillStyle = `rgba(198,245,151,${0.18 + i * 0.082})`;
        ctx.fill();
      }
      surfacePaths(coasts, 'rgba(204,232,172,0.23)', 0.6);

      function arcPath(line: Float32Array, color: string, lineWidth: number) {
        ctx.beginPath();
        let pen = false;
        for (let i = 0; i < line.length; i += 3) {
          project(line[i], line[i + 1], line[i + 2], point);
          if (occluded(point)) { pen = false; continue; }
          const x = cx + point[0] * radius, y = cy - point[1] * radius;
          if (pen) ctx.lineTo(x, y); else ctx.moveTo(x, y);
          pen = true;
        }
        ctx.strokeStyle = color;
        ctx.lineWidth = lineWidth;
        ctx.stroke();
      }

      arcPath(orbit, 'rgba(190,220,163,0.19)', 0.65);
      const activeRoute = selected === 0 ? 0 : selected - 1;
      // Inactive connection first; the selected connection is the visual focus.
      arcPath(routes[1 - activeRoute], 'rgba(198,245,130,0.24)', 0.8);
      arcPath(routes[activeRoute], 'rgba(198,245,130,0.77)', 1.15);

      const route = routes[activeRoute];
      const routePosition = Math.round((0.25 + progress * 0.6) * 120) * 3;
      project(route[routePosition], route[routePosition + 1], route[routePosition + 2], point);
      if (!occluded(point)) {
        const x = cx + point[0] * radius, y = cy - point[1] * radius;
        ctx.beginPath();
        ctx.arc(x, y, 3 * dotScale, 0, TAU);
        ctx.fillStyle = '#f3f1e9';
        ctx.fill();
        ctx.beginPath();
        ctx.arc(x, y, 7 * dotScale, 0, TAU);
        ctx.strokeStyle = 'rgba(243,241,233,0.2)';
        ctx.lineWidth = 0.7;
        ctx.stroke();
      }

      const markers = PLACES.map((location, index) => {
        const world = geographic(location.longitude, location.latitude);
        const view: Vector = [0, 0, 0];
        project(...world, view);
        return { view, index, location };
      }).sort((first, second) => first.view[2] - second.view[2]);

      for (const marker of markers) {
        if (marker.view[2] <= 0.06) continue;
        const isSelected = marker.index === selected;
        const x = cx + marker.view[0] * radius;
        const y = cy - marker.view[1] * radius;
        const size = isSelected ? 5 : 2.6;
        ctx.beginPath();
        ctx.arc(x, y, size * dotScale, 0, TAU);
        ctx.fillStyle = isSelected ? '#c6f582' : '#cedebc';
        ctx.fill();
        if (!isSelected) continue;
        ctx.beginPath();
        ctx.arc(x, y, 13 * dotScale, 0, TAU);
        ctx.strokeStyle = 'rgba(198,245,130,0.48)';
        ctx.lineWidth = 0.8;
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(x, y, 20 * dotScale, 0, TAU);
        ctx.strokeStyle = 'rgba(198,245,130,0.12)';
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(x, y, 1.8 * dotScale, 0, TAU);
        ctx.fillStyle = '#0b100c';
        ctx.fill();

        const labelWidth = Math.min(106, width * 0.28);
        const labelX = clamp(x + 23 * dotScale, 12, width - labelWidth - 12);
        const labelY = clamp(y - 29 * dotScale, 20, height - 20);
        ctx.beginPath();
        ctx.moveTo(x + 9 * dotScale, y - 9 * dotScale);
        ctx.lineTo(labelX - 5, labelY + 4);
        ctx.lineTo(labelX + labelWidth, labelY + 4);
        ctx.strokeStyle = 'rgba(198,245,130,0.4)';
        ctx.lineWidth = 0.65;
        ctx.stroke();
        ctx.font = `${Math.max(8, 9 * dotScale)}px ui-monospace, SFMono-Regular, Menlo, monospace`;
        ctx.fillStyle = '#e4eddb';
        ctx.fillText(marker.location.name, labelX, labelY, labelWidth);
      }

      // A few precise rim ticks retain the atlas / measuring-instrument motif.
      ctx.beginPath();
      for (let i = 0; i < 72; i++) {
        const angle = i / 72 * TAU;
        const tickRadius = radius * 1.075;
        const length = i % 6 === 0 ? 5 : 2;
        ctx.moveTo(cx + Math.cos(angle) * tickRadius, cy + Math.sin(angle) * tickRadius);
        ctx.lineTo(cx + Math.cos(angle) * (tickRadius + length), cy + Math.sin(angle) * (tickRadius + length));
      }
      ctx.strokeStyle = 'rgba(192,215,174,0.2)';
      ctx.lineWidth = 0.65;
      ctx.stroke();

      // Preserve the fallback until a usable geographical frame actually exists.
      if (!painted) { painted = true; setReady(true); }
    }

    function drawFrame(time: number) {
      frame = 0;
      if (disposed || contextLost || !visible || document.hidden) return;
      const delta = lastTime ? Math.min((time - lastTime) / 1000, 0.05) : 1 / 60;
      lastTime = time;
      if (drag) {
        // Pointer movement already owns the presentation. One coalesced frame
        // paints it directly, without the scroll/destination smoothing filter.
        render();
        lastTime = 0;
        return;
      }
      if (velocityYaw || velocityPitch) {
        momentumTime += delta;
        yaw += velocityYaw * delta;
        const nextPitch = clamp(pitch + velocityPitch * delta, -78 * DEG, 78 * DEG);
        if (nextPitch === pitch) velocityPitch = 0;
        pitch = nextPitch;
        const decay = Math.exp(-delta / 0.14);
        velocityYaw *= decay;
        velocityPitch *= decay;
        if (momentumTime >= 0.45 || Math.abs(velocityYaw) + Math.abs(velocityPitch) < 0.015) stopMomentum();
        rememberManualView();
        render();
        if (velocityYaw || velocityPitch) wake(); else lastTime = 0;
        return;
      }
      const blend = reduceMotion ? 1 : 1 - Math.exp(-delta / 0.15);
      const visualProgress = reduceMotion ? 0 : targetProgress;
      yaw += (targetYaw - yaw) * blend;
      pitch += (targetPitch - pitch) * blend;
      roll += (targetRoll - roll) * blend;
      progress += (visualProgress - progress) * blend;
      const moving = Math.abs(targetYaw - yaw) + Math.abs(targetPitch - pitch)
        + Math.abs(targetRoll - roll) + Math.abs(visualProgress - progress) > 0.00025;
      if (!moving) { yaw = targetYaw; pitch = targetPitch; roll = targetRoll; progress = visualProgress; }
      render();
      if (moving) wake(); else lastTime = 0;
    }

    function finishPointer(allowMomentum: boolean, time = performance.now()) {
      const ended = drag;
      if (!ended) return;
      drag = null;
      delete host!.dataset.dragging;
      try {
        if (host!.hasPointerCapture?.(ended.pointerId)) host!.releasePointerCapture(ended.pointerId);
      } catch { /* The browser may already have cancelled this pointer. */ }
      rememberManualView();
      if (allowMomentum && ended.committed && !reduceMotion) {
        const recent = ended.samples.filter((sample) => time - sample.time <= 100);
        const first = recent[0];
        const last = recent[recent.length - 1];
        if (recent.length >= 2 && time - last.time < 70) {
          const elapsed = (time - first.time) / 1000;
          if (elapsed >= 0.012) {
            velocityYaw = clamp((yaw - first.yaw) / elapsed, -1.8, 1.8);
            velocityPitch = ended.touch ? 0 : clamp((pitch - first.pitch) / elapsed, -1.3, 1.3);
            momentumTime = 0;
          }
        }
      }
      wake();
    }

    function cancelInteraction() {
      const wasMoving = Boolean(drag || velocityYaw || velocityPitch);
      stopMomentum();
      finishPointer(false);
      if (wasMoving) rememberManualView();
    }

    function onPointerDown(event: PointerEvent) {
      if (!loaded || contextLost || !visible || !event.isPrimary || drag) return;
      if (event.pointerType !== 'touch' && event.button !== 0) return;
      stopMomentum();
      stop();
      rememberManualView();
      const touch = event.pointerType === 'touch';
      drag = {
        pointerId: event.pointerId, touch, committed: !touch,
        startX: event.clientX, startY: event.clientY,
        lastX: event.clientX, lastY: event.clientY,
        samples: [{ time: event.timeStamp, yaw, pitch }],
      };
      try { host!.setPointerCapture(event.pointerId); } catch { drag = null; return; }
      if (!touch) {
        event.preventDefault();
        host!.focus({ preventScroll: true });
        host!.dataset.dragging = 'true';
      }
    }

    function onPointerMove(event: PointerEvent) {
      if (!drag || drag.pointerId !== event.pointerId) return;
      if (!drag.committed) {
        const x = Math.abs(event.clientX - drag.startX);
        const y = Math.abs(event.clientY - drag.startY);
        if (Math.max(x, y) < 6) return;
        if (y > x) { finishPointer(false); return; }
        if (x < y * 1.15) return;
        drag.committed = true;
        host!.dataset.dragging = 'true';
        host!.focus({ preventScroll: true });
      }
      const sensitivity = 2.66 / Math.max(width, 240);
      yaw -= (event.clientX - drag.lastX) * sensitivity;
      // Touch reserves its vertical axis for document scrolling.
      if (!drag.touch) pitch = clamp(pitch + (event.clientY - drag.lastY) * sensitivity, -78 * DEG, 78 * DEG);
      drag.lastX = event.clientX;
      drag.lastY = event.clientY;
      drag.samples.push({ time: event.timeStamp, yaw, pitch });
      drag.samples = drag.samples.filter((sample) => event.timeStamp - sample.time <= 100).slice(-24);
      rememberManualView();
      wake();
    }

    function onPointerUp(event: PointerEvent) {
      if (drag?.pointerId === event.pointerId) finishPointer(true, event.timeStamp);
    }

    function onPointerCancel(event: PointerEvent) {
      if (drag?.pointerId === event.pointerId) cancelInteraction();
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.target !== host || !loaded || contextLost || event.altKey || event.ctrlKey || event.metaKey) return;
      if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'Escape'].includes(event.key)) return;
      event.preventDefault();
      cancelInteraction();
      if (event.key === 'Escape') {
        rememberManualView();
        stop();
        return;
      }
      if (event.key === 'Home') {
        manualYaw = 0;
        manualPitch = 0;
        updateTargets();
      } else {
        const step = (event.shiftKey ? 15 : 7) * DEG;
        if (event.key === 'ArrowLeft') yaw += step;
        if (event.key === 'ArrowRight') yaw -= step;
        if (event.key === 'ArrowUp') pitch = clamp(pitch - step, -78 * DEG, 78 * DEG);
        if (event.key === 'ArrowDown') pitch = clamp(pitch + step, -78 * DEG, 78 * DEG);
        rememberManualView();
      }
      wake();
    }

    function onVisibility() {
      if (document.hidden) { cancelInteraction(); stop(); } else readScroll();
    }

    function onScroll() {
      cancelInteraction();
      if (!reduceMotion) readScroll();
    }

    function onWheel() { cancelInteraction(); }
    function onWindowBlur() { cancelInteraction(); stop(); }

    function onContextLost(event: Event) {
      event.preventDefault();
      cancelInteraction();
      contextLost = true;
      painted = false;
      setReady(false);
      stop();
    }

    function onContextRestored() { contextLost = false; resize(); }

    controlRef.current = {
      update(nextDestination, nextReducedMotion) {
        const nextSelected = destinationIndex(nextDestination);
        cancelInteraction();
        if (nextSelected !== selected) { manualYaw = 0; manualPitch = 0; }
        selected = nextSelected;
        reduceMotion = nextReducedMotion;
        readScroll();
      },
    };

    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(host);
    const intersectionObserver = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      if (visible) readScroll(); else { cancelInteraction(); stop(); }
    }, { rootMargin: '40px' });
    intersectionObserver.observe(host);
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('wheel', onWheel, { passive: true });
    window.addEventListener('blur', onWindowBlur);
    window.addEventListener('resize', resize, { passive: true });
    document.addEventListener('visibilitychange', onVisibility);
    host.addEventListener('pointerdown', onPointerDown);
    host.addEventListener('pointermove', onPointerMove, { passive: true });
    host.addEventListener('pointerup', onPointerUp);
    host.addEventListener('pointercancel', onPointerCancel);
    host.addEventListener('lostpointercapture', onPointerCancel);
    host.addEventListener('keydown', onKeyDown);
    canvas.addEventListener('contextlost', onContextLost);
    canvas.addEventListener('contextrestored', onContextRestored);
    resize();

    fetch(`${ASSET_ROOT}atlas-land.json`, { signal: abort.signal })
      .then((response) => { if (!response.ok) throw new Error('Land data unavailable'); return response.json() as Promise<LandAsset>; })
      .then((data) => {
        if (disposed || !Array.isArray(data.points) || !Array.isArray(data.coasts)) return;
        points = geographicArray(data.points);
        projected = new Float32Array(points.length);
        coasts = data.coasts.map(geographicArray);
        loaded = points.length > 0;
        wake();
      })
      .catch(() => { /* The selected local SVG remains the composed fallback. */ });

    return () => {
      disposed = true;
      cancelInteraction();
      controlRef.current = null;
      abort.abort();
      stop();
      resizeObserver.disconnect();
      intersectionObserver.disconnect();
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('wheel', onWheel);
      window.removeEventListener('blur', onWindowBlur);
      window.removeEventListener('resize', resize);
      document.removeEventListener('visibilitychange', onVisibility);
      host.removeEventListener('pointerdown', onPointerDown);
      host.removeEventListener('pointermove', onPointerMove);
      host.removeEventListener('pointerup', onPointerUp);
      host.removeEventListener('pointercancel', onPointerCancel);
      host.removeEventListener('lostpointercapture', onPointerCancel);
      host.removeEventListener('keydown', onKeyDown);
      canvas.removeEventListener('contextlost', onContextLost);
      canvas.removeEventListener('contextrestored', onContextRestored);
    };
  }, []);

  useEffect(() => { controlRef.current?.update(destination, reducedMotion); }, [destination, reducedMotion]);

  return (
    <div
      ref={hostRef}
      className={`ag-atlas${ready ? ' ag-atlas--ready' : ''}`}
      role={ready ? 'group' : 'img'}
      tabIndex={ready ? 0 : undefined}
      aria-label={`${ready ? 'Globe explorer' : 'Globe preview'}. Chosen destination: ${place.name}.`}
      aria-describedby={instructionId}
      aria-keyshortcuts={ready ? 'ArrowLeft ArrowRight ArrowUp ArrowDown Home' : undefined}
    >
      <div className="ag-atlas__underlay" aria-hidden="true" />
      <img className="ag-atlas__fallback" src={`${ASSET_ROOT}atlas-${place.slug}.svg`} alt="" draggable="false" />
      <canvas ref={canvasRef} className="ag-atlas__canvas" aria-hidden="true" />
      <span className="ag-atlas__cardinal ag-atlas__cardinal--north" aria-hidden="true">N</span>
      <span className="ag-atlas__cardinal ag-atlas__cardinal--south" aria-hidden="true">S</span>
      <span className="ag-atlas__cross ag-atlas__cross--left" aria-hidden="true">+</span>
      <span className="ag-atlas__cross ag-atlas__cross--right" aria-hidden="true">+</span>
      <span className="ag-atlas__hint" aria-hidden="true">
        <span className="ag-atlas__hint-pointer">{ready ? 'Drag to explore' : 'Globe preview'}</span>
        <span className="ag-atlas__hint-touch">{ready ? 'Swipe sideways to explore' : 'Globe preview'}</span>
        <span className="ag-atlas__hint-keyboard">Arrow keys to explore · Home to reset</span>
      </span>
      <span id={instructionId} className="ag-atlas__instructions">
        {ready
          ? 'Drag to rotate the globe, or use the arrow keys. Hold Shift for larger steps. Home restores the chosen destination view; Escape stops motion. On touch screens, swipe sideways to rotate and swipe vertically to scroll the page. Exploring changes the view only, not your chosen destination. Changing the chosen destination recenters the view.'
          : 'Static view of the chosen destination. The destination buttons below remain available.'}
      </span>
    </div>
  );
}
