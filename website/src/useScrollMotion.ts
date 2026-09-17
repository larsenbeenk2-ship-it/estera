import { useEffect } from 'react';

const clamp = (value: number) => Math.max(0, Math.min(1, value));

/** Native-scroll choreography. Measure stable wrappers; animate their children. */
export function useScrollMotion(reducedMotion: boolean) {
  useEffect(() => {
    const root = document.getElementById('main');
    if (!root) return;
    const scenes = [...root.querySelectorAll<HTMLElement>('[data-scroll-scene]')].map(element => ({
      element, top: 0, progress: 1, target: 1, lockedAt: null as number | null,
      route: element.querySelector<SVGPathElement>('.art-route'),
      car: element.querySelector<SVGGElement>('.art-car'),
      routeLength: 0,
    }));
    let frame = 0;
    let previousTime = 0;
    let viewport = window.innerHeight;

    const paint = (scene: typeof scenes[number]) => {
      const p = scene.progress;
      scene.element.style.setProperty('--scene-progress', p.toFixed(5));
      if (scene.route && scene.car && scene.routeLength > 0) {
        const distance = p * scene.routeLength;
        const point = scene.route.getPointAtLength(distance);
        const before = scene.route.getPointAtLength(Math.max(0, distance - .5));
        const after = scene.route.getPointAtLength(Math.min(scene.routeLength, distance + .5));
        const angle = Math.atan2(after.y - before.y, after.x - before.x) * 180 / Math.PI;
        scene.car.setAttribute('transform', `translate(${point.x} ${point.y}) rotate(${angle})`);
      }
    };

    const setTargets = () => {
      for (const scene of scenes) {
        // Settle while still in view, leaving controls and copy stationary to use.
        scene.target = reducedMotion ? 1 : scene.lockedAt ?? clamp((window.scrollY + viewport * .96 - scene.top) / (viewport * .72));
      }
    };
    const draw = (time: number) => {
      frame = 0;
      if (document.hidden) return;
      const delta = previousTime ? Math.min((time - previousTime) / 1000, .05) : 1 / 60;
      previousTime = time;
      const blend = 1 - Math.exp(-delta * 17);
      let moving = false;
      for (const scene of scenes) {
        const difference = scene.target - scene.progress;
        if (Math.abs(difference) > .0005) {
          scene.progress += difference * blend;
          moving = true;
        } else scene.progress = scene.target;
        paint(scene);
      }
      if (moving) frame = requestAnimationFrame(draw);
      else previousTime = 0;
    };
    const schedule = () => {
      setTargets();
      if (!frame && !document.hidden && !reducedMotion) frame = requestAnimationFrame(draw);
    };
    const measure = () => {
      viewport = window.innerHeight;
      for (const scene of scenes) {
        scene.top = scene.element.getBoundingClientRect().top + window.scrollY;
        scene.routeLength = scene.route?.getTotalLength() ?? 0;
      }
      schedule();
    };
    // Freeze under the pointer; keyboard focus can settle without moving a click target.
    const engage = (event: Event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      const active = target.closest('[data-scroll-scene]');
      const scene = scenes.find(item => item.element === active);
      if (!scene || scene.lockedAt !== null) return;
      scene.lockedAt = event.type === 'pointerdown' ? scene.progress : 1;
      scene.progress = scene.target = scene.lockedAt;
      paint(scene);
    };
    const visibility = () => {
      cancelAnimationFrame(frame);
      frame = 0;
      previousTime = 0;
      if (!document.hidden) measure();
    };

    measure();
    for (const scene of scenes) {
      scene.progress = scene.target;
      paint(scene);
    }
    if (reducedMotion) {
      cancelAnimationFrame(frame);
      return () => { for (const scene of scenes) scene.element.style.removeProperty('--scene-progress'); };
    }
    const resize = new ResizeObserver(measure);
    resize.observe(root);
    window.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', measure);
    document.addEventListener('visibilitychange', visibility);
    root.addEventListener('focusin', engage);
    root.addEventListener('pointerdown', engage, { passive: true });
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      window.removeEventListener('scroll', schedule);
      window.removeEventListener('resize', measure);
      document.removeEventListener('visibilitychange', visibility);
      root.removeEventListener('focusin', engage);
      root.removeEventListener('pointerdown', engage);
      for (const scene of scenes) scene.element.style.removeProperty('--scene-progress');
    };
  }, [reducedMotion]);
}
