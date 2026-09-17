import { useEffect, useRef, useState } from 'react';
import { ArrowDown, ArrowDownToLine, ArrowRight, ArrowUpRight, Check, ChevronDown, Code2, Compass, Ghost, MapPin, Menu, Monitor, MoveUpRight, Route, Star, X } from 'lucide-react';
import AtlasGlobe from './components/AtlasGlobe';
import LocationPreview from './components/LocationPreview';
import JourneyEnd, { AtlasFooter } from './components/JourneyEnd';
import { useScrollMotion } from './useScrollMotion';
import { DOWNLOAD_URL, GITHUB_URL, RELEASE_URL, RELEASE_VERSION } from './release';

const destinations = [
  { name: 'San Francisco', short: 'San Francisco', coordinates: '37.7749° N / 122.4194° W', caption: 'California, United States', code: 'SFO' },
  { name: 'Tokyo', short: 'Tokyo', coordinates: '35.6762° N / 139.6503° E', caption: 'Kantō, Japan', code: 'TYO' },
  { name: 'Paris', short: 'Paris', coordinates: '48.8566° N / 2.3522° E', caption: 'Île-de-France, France', code: 'PAR' },
];

function useMotionPreference() {
  const [reduced, setReduced] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    const change = () => setReduced(query.matches);
    query.addEventListener('change', change);
    return () => query.removeEventListener('change', change);
  }, []);
  return reduced;
}

function Brand() {
  return <a className="brand" href="#top" aria-label="Estera home"><Ghost size={29} strokeWidth={1.6} aria-hidden="true"/><span>estera<span className="brand-period">.</span></span></a>;
}

function SectionLabel({ number, children }: { number: string; children: React.ReactNode }) {
  return <div className="section-label"><span>{number}</span><span className="label-line"/><span>{children}</span></div>;
}

function EditionArtwork({ pro = false }: { pro?: boolean }) {
  return <div className={`edition-art ${pro ? 'edition-art-pro' : ''}`} aria-hidden="true"><svg viewBox="0 0 440 150" fill="none">
    <path className="art-street" d="M0 30H440M0 78H440M0 126H440M57 0V150M117 0V150M177 0V150M237 0V150M297 0V150M357 0V150M417 0V150"/>
    {pro ? <><path className="art-route" pathLength="1" d="M57 104V82Q57 78 64 78H110Q117 78 117 71V36Q117 30 124 30H290Q297 30 297 37V71Q297 78 304 78H375"/><circle className="art-end" cx="57" cy="104" r="4"/><circle className="art-end" cx="375" cy="78" r="5"/><g className="art-car" transform="translate(232 30)"><rect x="-15" y="-10" width="30" height="20" rx="6"/><path d="M-6-4H6L8 3H-8Z"/></g></> : <><circle className="art-radius" cx="220" cy="79" r="58"/><circle className="art-radius" cx="220" cy="79" r="38"/><circle className="art-radius" cx="220" cy="79" r="19"/><path className="art-pin" d="M220 29c-11 0-19 8-19 19 0 14 19 32 19 32s19-18 19-32c0-11-8-19-19-19Z"/><circle className="art-pin-eye" cx="220" cy="48" r="6"/></>}
  </svg></div>;
}

function ReleaseDialog({ open, close }: { open: boolean; close: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (open && !dialogRef.current?.open) dialogRef.current?.showModal();
    if (!open && dialogRef.current?.open) dialogRef.current.close();
  }, [open]);
  return <dialog className="release-dialog" ref={dialogRef} onCancel={close} onClose={close} aria-labelledby="release-title" onClick={event => {
    if (event.target !== event.currentTarget) return;
    const rect = event.currentTarget.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) close();
  }}>
    <button className="icon-button dialog-close" aria-label="Close download details" onClick={close}><X size={22}/></button>
    <span className="dialog-symbol"><Ghost size={36} strokeWidth={1.3}/></span>
    <span className="eyebrow">YOUR NEXT DESTINATION</span><h2 id="release-title">Estera for Mac<span>.</span></h2>
    <p>One free desktop app. Pins, routes, and the whole journey. Open source under the GPL.</p>
    <div className="release-state"><span className="status-dot"/><div><strong>v{RELEASE_VERSION} prerelease · Apple Silicon · macOS 15+</strong><p>This early release is not signed with a Developer ID or notarized by Apple. Read the release notes before installing.</p></div></div>
    <ol className="install-steps"><li>Download and unzip the file.</li><li>Drag Estera into Applications, then open it.</li><li>If macOS blocks it, go to System Settings → Privacy &amp; Security and choose Open Anyway for Estera, only if you trust this download.</li><li>Connect your iPhone by USB and follow the app’s setup steps.</li></ol>
    <a href={DOWNLOAD_URL} className="button button-primary">Download for Mac <ArrowDownToLine size={17}/></a>
    <div className="release-links"><a href={RELEASE_URL}>Release notes <ArrowUpRight size={14}/></a><a href={GITHUB_URL}>Star on GitHub <Star size={14}/></a></div>
    <p className="release-support">Enjoy Estera? A star helps others find it. Starring is optional; no GitHub account is needed to download. Windows is a source-only preview.</p>
  </dialog>;
}

const questions = [
  ['What does Estera do?', 'Estera is an iPhone location simulator controlled from your Mac. Choose a point on the map or create a simulated journey with walking, cycling, and driving. Your physical location stays the same.'],
  ['How do I connect my iPhone?', 'Start with a USB connection, unlock your iPhone, and trust your Mac when prompted. Estera guides you through the remaining device setup. See the GitHub release notes for current compatibility and known limitations.'],
  ['Are pins and journeys separate editions?', 'No. Estera is one free, open-source app. Single locations, walking, cycling, driving, stops, speed controls, saved journeys, and GPX files are all included.'],
  ['Does this website change my location?', 'No. The globe and route preview are interactive illustrations. They do not access your location, connect to your phone, or make changes to any device.'],
  ['How do I install the desktop app?', 'Download the macOS prerelease for Apple Silicon and macOS 15 or later. Unzip it, drag Estera into Applications, and open it. The app is not Developer ID signed or notarized; if macOS blocks it, use Open Anyway in System Settings → Privacy & Security only if you trust the download. Windows currently has a source-only preview.'],
  ['Do I need to star the GitHub repository?', 'Starring is a welcome way to support Estera, and it is entirely optional. Open the repository, sign in to GitHub, and select Star. Downloading and using the app does not require a GitHub account.'],
];

export default function App() {
  const [menuOpen, setMenuOpen] = useState(false);
  const [releaseOpen, setReleaseOpen] = useState(false);
  const [destination, setDestination] = useState(0);
  const reducedMotion = useMotionPreference();
  useScrollMotion(reducedMotion);
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') setMenuOpen(false); };
    if (menuOpen) window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [menuOpen]);
  const place = destinations[destination];

  return <>
    <a className="skip-link" href="#main">Skip to content</a>
    <div id="top"/>
    <header className="site-header wrap"><Brand/>
      <nav id="navigation" aria-label="Main navigation" className={menuOpen ? 'navigation is-open' : 'navigation'}>
        <a href="#experience" onClick={() => setMenuOpen(false)}>The experience</a><a href="#editions" onClick={() => setMenuOpen(false)}>One free app</a><a href="#questions" onClick={() => setMenuOpen(false)}>Good to know</a>
      </nav>
      <div className="header-actions"><a className="button button-secondary button-small" href={GITHUB_URL}>GitHub <ArrowUpRight size={16}/></a><button className="icon-button mobile-menu" aria-expanded={menuOpen} aria-controls="navigation" aria-label={menuOpen ? 'Close navigation' : 'Open navigation'} onClick={() => setMenuOpen(!menuOpen)}>{menuOpen ? <X size={22}/> : <Menu size={22}/>}</button></div>
    </header>
    <main id="main">
      <section className="hero" id="atlas-hero" aria-labelledby="hero-title">
        <div className="hero-stage"><div className="hero-grid wrap">
          <div className="hero-copy"><div className="eyebrow hero-eyebrow"><span className="status-dot"/>LOCATION, ON YOUR TERMS</div>
            <h1 id="hero-title">Your iPhone.<br/>Somewhere<br/><em>else.</em><span className="hero-asterisk" aria-hidden="true">✳</span></h1>
            <p>A little freedom to be anywhere.<br/>Simulate your iPhone’s location from your Mac.<br/>One free, open-source desktop app.</p>
            <div className="hero-actions"><button type="button" className="button button-primary" onClick={() => setReleaseOpen(true)}>Download for Mac <ArrowDownToLine size={18}/></button><a className="text-link" href="#preview">Take a test drive <ArrowRight size={16}/></a></div>
            <span className="hero-note"><Monitor size={13}/> Apple Silicon · macOS 15+ · Prerelease</span>
          </div>
          <div className="hero-globe">
            <div className="globe-topline"><span>THE WORLD IS YOURS TO EXPLORE</span><Compass size={18} strokeWidth={1.2}/></div>
            <AtlasGlobe destination={destination} reducedMotion={reducedMotion}/>
            <div className="destination-readout" aria-live="polite" aria-atomic="true"><div><MapPin size={14}/><span>{place.coordinates}</span></div><strong>{place.name}<span>{place.code}</span></strong><p>{place.caption}</p></div>
            <div className="destination-switch" role="group" aria-label="Explore a destination">{destinations.map((item, index) => <button key={item.code} type="button" aria-pressed={destination === index} onClick={() => setDestination(index)}><span className="destination-dot"/>{item.short}<ArrowUpRight size={12}/></button>)}</div>
          </div>
        </div><div className="hero-bottom wrap"><a href="#experience"><span className="scroll-circle"><ArrowDown size={17}/></span><span>SCROLL TO CHANGE YOUR PERSPECTIVE</span></a><span className="hero-index">01 — 04 <span> / </span> ESTERA EXPLORER</span></div></div>
      </section>

      <section className="experience-section wrap" id="experience" aria-labelledby="experience-title">
        <SectionLabel number="01">A DIFFERENT POINT OF VIEW</SectionLabel>
        <div className="experience-heading"><h2 id="experience-title">Same you.<br/><span>New coordinates.</span></h2><div className="experience-intro"><MoveUpRight size={42} strokeWidth={1}/><p>Across the street. Across the world.<br/>Estera puts a different place within reach, without taking you out of your moment.</p></div></div>
        <div className="feature-sequence">
          <article><div className="feature-top"><span>01 / CONNECT</span><Monitor size={19} strokeWidth={1.4}/></div><h3>Your Mac. Your iPhone.</h3><p>Connect your iPhone and follow the guided setup. Your Mac becomes the starting point.</p></article>
          <article><div className="feature-top"><span>02 / CHOOSE</span><MapPin size={19} strokeWidth={1.4}/></div><h3>A place that’s calling.</h3><p>Find an address, drop a pin, or choose coordinates. From familiar corners to somewhere new.</p></article>
          <article><div className="feature-top"><span>03 / EXPLORE</span><Route size={19} strokeWidth={1.4}/></div><h3>Stay. Or take a detour.</h3><p>Keep things simple with a single location. Or bring a whole journey to life. Both are included.</p></article>
        </div>
      </section>

      <section className="demo-section" id="preview" aria-labelledby="demo-title"><div className="wrap">
        <div className="demo-heading"><div><SectionLabel number="02">THE WORLD, AT YOUR FINGERTIPS</SectionLabel><h2 id="demo-title">A little test drive.<br/><em>A lot of possibility.</em></h2></div><p>Drop a pin to pick a place.<br/>Try Journey to scroll the scenic route.<br/><span>A little scroll goes a long way.</span></p></div>
        <div className="preview-scene" data-scroll-scene="preview"><div className="product-showcase"><div className="product-edge"/><LocationPreview reducedMotion={reducedMotion}/></div></div>
        <div className="preview-caption"><span><span className="status-dot"/> INTERACTIVE PREVIEW</span><p>An illustrative demo. Your phone stays exactly as it is.</p><span>37°46′ N &nbsp; 122°25′ W</span></div>
        <div className="journey-notes"><div><span className="tiny-cross">+</span><p><strong>Choose your pace.</strong> Walk, cycle, or drive.</p></div><div><span className="tiny-cross">+</span><p><strong>Make a few stops.</strong> The detour is the point.</p></div><div><span className="tiny-cross">+</span><p><strong>Keep a favorite.</strong> Save it. Go again.</p></div></div>
      </div></section>

      <section className="editions-band" id="editions" aria-labelledby="editions-title"><div className="wrap">
        <SectionLabel number="03">TWO WAYS TO EXPLORE. ONE FREE APP.</SectionLabel>
        <div className="editions-heading"><h2 id="editions-title">Start with a pin.<br/><em>See where it takes you.</em></h2><span className="editions-seal"><Ghost size={34} strokeWidth={1.2}/><span>A LITTLE MORE<br/>FREEDOM.</span></span></div>
        <div className="edition-grid" data-scroll-scene="editions">
          <article className="edition-card open-card"><div className="edition-card-heading"><div className="edition-name"><h3>Pin<span>.</span></h3><span className="edition-badge"><Code2 size={13}/> OPEN SOURCE</span></div><p>One place. Endless possibilities.</p></div><EditionArtwork/>
            <div className="edition-card-body"><div className="edition-price"><strong>Free to explore.</strong><span>The essentials, beautifully simple.</span></div><ul><li><Check size={15}/> Set a location anywhere</li><li><Check size={15}/> Search places and coordinates</li><li><Check size={15}/> Simple location controls</li><li><Check size={15}/> Open-source code</li></ul><button type="button" className="button button-dark" onClick={() => setReleaseOpen(true)}>Download Estera <ArrowDownToLine size={17}/></button><small>macOS 15+ · Apple Silicon · Prerelease</small></div>
          </article>
          <article className="edition-card pro-card"><div className="edition-card-heading"><div className="edition-name"><h3>Journey<span>.</span></h3><span className="edition-badge"><Route size={13}/> INCLUDED FREE</span></div><p>For everything between here and there.</p></div><EditionArtwork pro/>
            <div className="edition-card-body"><div className="edition-price"><strong>Go a little further.</strong><span>More control. More ways to move.</span></div><ul><li><Check size={15}/> All the location essentials</li><li><Check size={15}/> Walking, cycling, and driving</li><li><Check size={15}/> Multiple stops, speed, and playback</li><li><Check size={15}/> Saved journeys and GPX files</li></ul><a className="button button-primary" href={GITHUB_URL}>Star on GitHub <Star size={17}/></a><small>A little support. Always optional.</small></div>
          </article>
        </div><p className="edition-footnote">Every feature above is part of the same free app. No subscription or GitHub account required.<br/>The macOS prerelease is not Developer ID signed or notarized. Windows is a source-only preview.</p>
      </div></section>

      <section className="faq-section wrap" id="questions" aria-labelledby="faq-title"><div className="faq-intro"><SectionLabel number="04">BEFORE YOU GO</SectionLabel><h2 id="faq-title">A few good<br/><em>questions.</em></h2><p>A little clarity for the road ahead.</p><Compass size={63} strokeWidth={.7} aria-hidden="true"/></div><div className="faq-list">{questions.map(([question, answer], index) => <details key={question}><summary><span className="faq-number">0{index + 1}</span><span>{question}</span><ChevronDown size={19}/></summary><p>{answer}</p></details>)}</div></section>

      <JourneyEnd destinations={destinations} destination={destination} onDestinationChange={setDestination}/>
    </main>
    <AtlasFooter onRelease={() => setReleaseOpen(true)}/>
    <ReleaseDialog open={releaseOpen} close={() => setReleaseOpen(false)}/>
  </>;
}
