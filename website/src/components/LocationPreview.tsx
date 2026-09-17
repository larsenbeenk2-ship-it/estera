import { memo, useEffect, useId, useMemo, useRef, useState } from 'react';
import { ArrowUpRight, Check, ChevronDown, MapPin, Mouse, Navigation, Pause, Play, RotateCcw, Search } from 'lucide-react';
import './LocationPreview.css';

type Point = readonly [number, number];
type Edition = 'open' | 'pro';

const ROUTES = [
  {
    name: 'Along the waterfront', start: 'Marina Green', finish: 'Ferry Building', distance: 4.2,
    points: [[285, 145], [395, 145], [395, 190], [505, 190], [505, 235], [615, 235], [670, 280], [670, 325], [725, 370], [725, 415]] as Point[],
  },
  {
    name: 'Across the city', start: 'Pacific Heights', finish: 'Union Square', distance: 3.1,
    points: [[175, 325], [285, 325], [285, 370], [395, 370], [395, 415], [505, 415], [505, 505], [450, 505]] as Point[],
  },
];

const PLACES = [
  { name: 'Marina Green', area: 'Marina District', point: [285, 145] as Point },
  { name: 'Union Square', area: 'Union Square', point: [450, 505] as Point },
  { name: 'Ferry Building', area: 'The Embarcadero', point: [725, 415] as Point },
];

function measureRoute(points: Point[]) {
  const lengths = points.slice(1).map((point, index) => Math.hypot(point[0] - points[index][0], point[1] - points[index][1]));
  return { lengths, total: lengths.reduce((sum, length) => sum + length, 0) };
}

function positionOnRoute(points: Point[], lengths: number[], total: number, progress: number): Point {
  let remaining = total * progress;
  for (let index = 0; index < lengths.length; index += 1) {
    if (remaining <= lengths[index]) {
      const fraction = remaining / lengths[index];
      return [points[index][0] + (points[index + 1][0] - points[index][0]) * fraction,
        points[index][1] + (points[index + 1][1] - points[index][1]) * fraction];
    }
    remaining -= lengths[index];
  }
  return points[points.length - 1];
}

/** Original San Francisco schematic; these streets illustrate the website demo. */
const MapArtwork = memo(function MapArtwork({ clipId }: { clipId: string }) {
  const coast = 'M-10-10H505L530 40 575 70 575 105 625 135 638 180 662 198 662 237 691 260 700 302 750 337 772 370 822 408 850 448 920 523V680H-10Z';
  return <>
    <defs><clipPath id={clipId}><path d={coast} /></clipPath></defs>
    <rect width="900" height="650" className="lp-water" />
    <path d={coast} className="lp-land" />
    <g clipPath={`url(#${clipId})`}>
      {Array.from({ length: 16 }, (_, column) => Array.from({ length: 15 }, (_, row) => (
        <rect key={`${column}-${row}`} x={column * 55 + 16} y={row * 45 + 16} width="42" height="32" rx="2" className="lp-block" />
      )))}
      <path d="M-10 53 112 78 166 129 151 207 109 248 9 229-10 174Z M225 104H382V133H225Z M524 245H563V277H524Z M415 515H445V542H415Z M57 434H157V490H57Z" className="lp-park" />
      <g className="lp-minor-roads">
        {Array.from({ length: 17 }, (_, index) => <path key={`v-${index}`} d={`M${10 + index * 55} 0V650`} />)}
        {Array.from({ length: 15 }, (_, index) => <path key={`h-${index}`} d={`M0 ${10 + index * 45}H900`} />)}
      </g>
      <g className="lp-roads">
        <path d="M0 145H615 M0 235H670 M0 325H725 M0 415H830 M0 505H880 M175 0V650 M395 0V650 M505 0V650 M615 0V650 M62 650 280 575 465 500 725 370" />
        <path d="M190 87 396 87 498 112 560 158 610 202 644 252 670 280 670 325 725 370 725 415 795 488 900 547" />
      </g>
      <g className="lp-street-labels">
        <text x="208" y="230">LOMBARD ST</text>
        <text x="35" y="320">BROADWAY</text>
        <text x="237" y="410">CALIFORNIA ST</text>
        <text x="209" y="500">GEARY BLVD</text>
        <text x="388" y="585" transform="rotate(-90 388 585)">VAN NESS AVE</text>
        <text x="608" y="553" transform="rotate(-90 608 553)">KEARNY ST</text>
        <text x="690" y="468" transform="rotate(45 690 468)">THE EMBARCADERO</text>
        <text x="188" y="615" transform="rotate(-23 188 615)">MARKET STREET</text>
      </g>
    </g>
    <path d="M579 80 593 58M609 116 634 91M648 175 675 153M674 220 699 201M711 293 744 269M762 351 795 326" className="lp-piers" />
    <g className="lp-district-labels">
      <text x="77" y="149">THE PRESIDIO</text>
      <text x="285" y="188">MARINA DISTRICT</text>
      <text x="438" y="270">RUSSIAN HILL</text>
      <text x="547" y="321">NORTH BEACH</text>
      <text x="229" y="288">PACIFIC HEIGHTS</text>
      <text x="546" y="389">CHINATOWN</text>
      <text x="667" y="503">FINANCIAL DISTRICT</text>
      <text x="451" y="552">UNION SQUARE</text>
      <text x="107" y="467">LAFAYETTE PARK</text>
    </g>
    <g className="lp-bay-label"><text x="760" y="129" transform="rotate(24 760 129)">San Francisco</text><text x="760" y="153" transform="rotate(24 760 153)">Bay</text></g>
    <g className="lp-landmarks">
      <circle cx="544" cy="260" r="3" /><text x="550" y="251">Coit Tower</text>
      <circle cx="757" cy="427" r="3" /><text x="769" y="431">Ferry Building</text>
      <text x="303" y="125">Marina Green</text>
    </g>
  </>;
});

export default function LocationPreview({ reducedMotion = false }: { reducedMotion?: boolean }) {
  const id = useId().replace(/:/g, '');
  const containerRef = useRef<HTMLDivElement>(null);
  const progressRef = useRef(0);
  const [edition, setEdition] = useState<Edition>('pro');
  const [routeIndex, setRouteIndex] = useState(0);
  const [speed, setSpeed] = useState(32);
  const [playing, setPlaying] = useState(false);
  const [driveMode, setDriveMode] = useState<'scroll' | 'manual'>('scroll');
  const [progress, setProgress] = useState(0);
  const [inView, setInView] = useState(false);
  const [pageVisible, setPageVisible] = useState(true);
  const [query, setQuery] = useState('');
  const [selectedPlace, setSelectedPlace] = useState(0);
  const [activePlace, setActivePlace] = useState(0);
  const [placeWasSet, setPlaceWasSet] = useState(false);
  const route = ROUTES[routeIndex];
  const geometry = useMemo(() => measureRoute(route.points), [route]);
  const point = edition === 'pro' ? positionOnRoute(route.points, geometry.lengths, geometry.total, progress) : PLACES[activePlace].point;
  const polyline = route.points.map((position) => position.join(',')).join(' ');
  const matchingPlaces = PLACES.map((place, index) => ({ ...place, index })).filter((place) => `${place.name} ${place.area}`.toLowerCase().includes(query.toLowerCase().trim()));
  const finished = progress >= 1;
  const scrollDriving = driveMode === 'scroll' && !reducedMotion;
  const before = positionOnRoute(route.points, geometry.lengths, geometry.total, Math.max(0, progress - .003));
  const after = positionOnRoute(route.points, geometry.lengths, geometry.total, Math.min(1, progress + .003));
  const carAngle = Math.atan2(after[1] - before[1], after[0] - before[0]) * 180 / Math.PI;

  useEffect(() => {
    if (!scrollDriving || edition !== 'pro' || !pageVisible) return;
    const container = containerRef.current;
    const scene = container?.closest<HTMLElement>('[data-scroll-scene="preview"]');
    if (!container || !scene) return;
    let frame = 0;
    let sceneTop = 0;
    let viewport = window.innerHeight;
    const paint = () => {
      frame = 0;
      if (document.hidden) return;
      // A full journey in roughly 240–320px; absolute progress reverses with scroll.
      // Measure the untransformed wrapper so the window's entrance cannot move the car.
      const travel = Math.max(240, Math.min(320, viewport * .36));
      const next = Math.max(0, Math.min(1, (window.scrollY + viewport * .62 - sceneTop) / travel));
      if (Math.abs(next - progressRef.current) < .00001) return;
      progressRef.current = next;
      setProgress(next);
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(paint); };
    const measure = () => {
      viewport = window.innerHeight;
      sceneTop = scene.getBoundingClientRect().top + window.scrollY;
      schedule();
    };
    const observer = new ResizeObserver(measure);
    observer.observe(document.getElementById('main') ?? scene);
    window.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', measure);
    measure();
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener('scroll', schedule);
      window.removeEventListener('resize', measure);
    };
  }, [scrollDriving, edition, routeIndex, pageVisible]);

  useEffect(() => {
    const onVisibility = () => {
      const visible = document.visibilityState === 'visible';
      setPageVisible(visible);
      if (!visible) setPlaying(false);
    };
    onVisibility();
    document.addEventListener('visibilitychange', onVisibility);
    const observer = new IntersectionObserver(([entry]) => {
      setInView(entry.isIntersecting);
      if (!entry.isIntersecting) setPlaying(false);
    }, { threshold: 0.08 });
    if (containerRef.current) observer.observe(containerRef.current);
    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      observer.disconnect();
    };
  }, []);

  useEffect(() => {
    if (!playing || scrollDriving || !pageVisible || !inView || edition !== 'pro') return;
    let animationId = 0;
    let previousTime = 0;
    let lastPaint = 0;
    const animate = (now: number) => {
      const elapsed = previousTime ? Math.min(now - previousTime, 100) / 1000 : 0;
      previousTime = now;
      progressRef.current = Math.min(1, progressRef.current + (elapsed * speed * 72) / (3600 * route.distance));
      if (now - lastPaint >= (reducedMotion ? 600 : 32) || progressRef.current >= 1) {
        lastPaint = now;
        setProgress(progressRef.current);
      }
      if (progressRef.current >= 1) {
        setPlaying(false);
        return;
      }
      animationId = requestAnimationFrame(animate);
    };
    animationId = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(animationId);
  }, [playing, scrollDriving, pageVisible, inView, edition, speed, route.distance, reducedMotion]);

  const reset = () => {
    setPlaying(false);
    setDriveMode('manual');
    progressRef.current = 0;
    setProgress(0);
  };

  const togglePlayback = () => {
    setDriveMode('manual');
    if (finished) {
      progressRef.current = 0;
      setProgress(0);
    }
    setPlaying((value) => !value);
  };

  return <div className="lp-window" ref={containerRef}>
    <div className="lp-titlebar">
      <span className="lp-window-dots" aria-hidden="true"><i /><i /><i /></span>
      <span className="lp-app-name"><Navigation size={13} strokeWidth={2.2} aria-hidden="true" />Estera</span>
      <span className="lp-demo-label">Website demo</span>
    </div>
    <div className="lp-workspace">
      <div className="lp-sidebar">
        <div className="lp-edition-switch" role="group" aria-label="Preview mode">
          <button type="button" aria-pressed={edition === 'open'} onClick={() => { setPlaying(false); setEdition('open'); }}>Pin</button>
          <button type="button" aria-pressed={edition === 'pro'} onClick={() => setEdition('pro')}>Journey</button>
        </div>
        {edition === 'pro' ? <div className="lp-controls">
          <div className="lp-field">
            <label htmlFor={`${id}-route`}>Route</label>
            <div className="lp-select-wrap">
              <select id={`${id}-route`} value={routeIndex} onChange={(event) => { reset(); setRouteIndex(Number(event.target.value)); }}>
                {ROUTES.map((preset, index) => <option key={preset.name} value={index}>{preset.name}</option>)}
              </select>
              <ChevronDown size={13} aria-hidden="true" />
            </div>
          </div>
          <div className="lp-itinerary">
            <div><span className="lp-stop-icon lp-stop-icon-start" aria-hidden="true" /><span><small>Start</small><strong>{route.start}</strong></span></div>
            <div><span className="lp-stop-icon lp-stop-icon-finish" aria-hidden="true" /><span><small>Finish</small><strong>{route.finish}</strong></span></div>
          </div>
          <div className="lp-speed">
            <label htmlFor={`${id}-speed`}>Playback speed <span><strong>{speed}</strong> km/h</span></label>
            <input id={`${id}-speed`} type="range" min="5" max="80" step="1" value={speed} onChange={(event) => setSpeed(Number(event.target.value))} aria-valuetext={`${speed} kilometers per hour`} />
            <div className="lp-range-limits"><span>5</span><span>80</span></div>
          </div>
          <div className="lp-playback">
            <button type="button" className="lp-primary" onClick={togglePlayback}>
              {playing ? <Pause size={13} fill="currentColor" aria-hidden="true" /> : <Play size={13} fill="currentColor" aria-hidden="true" />}
              {playing ? 'Pause' : finished ? 'Replay route' : 'Play route'}
            </button>
            <button type="button" className="lp-reset" onClick={reset} aria-label="Reset route" title="Reset route"><RotateCcw size={16} aria-hidden="true" /></button>
          </div>
          <p className="lp-control-note">{scrollDriving ? 'Scroll down to drive · up to rewind' : '72× preview speed'}</p>
          {!reducedMotion && <button type="button" className="lp-scroll-mode" aria-pressed={scrollDriving} onClick={() => { setPlaying(false); setDriveMode(scrollDriving ? 'manual' : 'scroll'); }}><Mouse size={13}/>{scrollDriving ? 'Scroll driving on' : 'Turn on scroll driving'}<span className="lp-mode-indicator" aria-hidden="true"/></button>}
        </div> : <div className="lp-controls">
          <div className="lp-field">
            <label htmlFor={`${id}-search`}>Find a demo place</label>
            <div className="lp-search"><Search size={14} aria-hidden="true" /><input id={`${id}-search`} type="search" placeholder="Search San Francisco" value={query} onChange={(event) => setQuery(event.target.value)} /></div>
          </div>
          <div className="lp-places" role="group" aria-label="Demo places">
            {matchingPlaces.map((place) => <button type="button" key={place.name} aria-pressed={selectedPlace === place.index} onClick={() => { setSelectedPlace(place.index); setPlaceWasSet(false); }}>
              <MapPin size={15} aria-hidden="true" /><span><strong>{place.name}</strong><small>{place.area}</small></span>{selectedPlace === place.index && <Check size={13} aria-hidden="true" />}
            </button>)}
            {matchingPlaces.length === 0 && <p className="lp-empty">No demo places found.</p>}
          </div>
          <button type="button" className="lp-primary lp-set-place" disabled={!matchingPlaces.some(place => place.index === selectedPlace)} onClick={() => { setActivePlace(selectedPlace); setPlaceWasSet(true); }}><MapPin size={14} aria-hidden="true" />Set preview location</button>
          <p className="lp-control-note" role="status">{placeWasSet ? `${PLACES[activePlace].name} selected` : 'Choose a place. Make it yours.'}</p>
        </div>}
        <div className="lp-sidebar-footer"><span className="lp-connection-dot" aria-hidden="true" />No device connected</div>
      </div>
      <div className="lp-map">
        <svg className="lp-map-art" viewBox="0 0 900 650" role="img" aria-label={edition === 'pro' ? `San Francisco schematic: ${route.start} to ${route.finish}` : `San Francisco schematic with location at ${PLACES[activePlace].name}`}>
          <MapArtwork clipId={`${id}-land`} />
          {edition === 'pro' && <>
            <polyline points={polyline} className="lp-route-outline" />
            <polyline points={polyline} className="lp-route" />
            <polyline points={polyline} className="lp-route-traveled" pathLength="1" strokeDasharray={`${progress} 1`} />
            <circle cx={route.points[0][0]} cy={route.points[0][1]} r="6" className="lp-endpoint" />
            <circle cx={route.points[route.points.length - 1][0]} cy={route.points[route.points.length - 1][1]} r="7" className="lp-endpoint lp-destination" />
          </>}
          <g transform={`translate(${point[0]} ${point[1]})`}>
            <circle r="26" className="lp-position-aura" />
            {edition === 'pro' ? <g className="lp-car" transform={`rotate(${carAngle})`}>
              <path className="lp-car-light" d="M16-5 44-15V15L16 5Z"/>
              <rect className="lp-car-tire" x="-11" y="-12" width="8" height="5" rx="2"/>
              <rect className="lp-car-tire" x="7" y="-12" width="8" height="5" rx="2"/>
              <rect className="lp-car-tire" x="-11" y="7" width="8" height="5" rx="2"/>
              <rect className="lp-car-tire" x="7" y="7" width="8" height="5" rx="2"/>
              <rect className="lp-car-body" x="-18" y="-9" width="36" height="18" rx="6"/>
              <path className="lp-car-glass" d="M3-6 9-5 11 0 9 5 3 6Z M-9-6-12-4V4L-9 6Z"/>
              <path className="lp-car-roof" d="M-7-6H1V6H-7Z"/>
              <path className="lp-car-headlight" d="M15-6V-3M15 3V6"/>
            </g> : <><circle r="12" className="lp-position-ring" /><circle r="7" className="lp-position-dot" /></>}
          </g>
        </svg>
        <div className="lp-map-heading"><span>San Francisco</span><small>California, United States</small></div>
        <div className="lp-compass" aria-label="North is up"><span>N</span><Navigation size={17} aria-hidden="true" /></div>
        <div className="lp-map-status"><span>{edition === 'pro' ? scrollDriving ? <Mouse size={14} aria-hidden="true"/> : <ArrowUpRight size={14} aria-hidden="true" /> : <MapPin size={14} aria-hidden="true" />}</span><strong>{edition === 'pro' ? finished ? scrollDriving ? 'Arrived. Scroll up to rewind.' : 'Route complete' : scrollDriving ? progress > 0 ? 'Following your scroll' : 'Scroll to drive' : playing ? 'Following the route' : progress > 0 ? 'Route paused' : 'Ready to explore' : PLACES[activePlace].name}</strong>{edition === 'pro' && <small>{Math.round(progress * 100)}%</small>}</div>
        <span className="lp-map-caption">Illustrative map</span>
      </div>
    </div>
  </div>;
}
