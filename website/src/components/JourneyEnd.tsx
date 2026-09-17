import { ArrowRight, ArrowUpRight, Compass, Ghost, MapPin, Monitor } from 'lucide-react';
import './JourneyEnd.css';
import { GITHUB_URL, RELEASE_URL, RELEASE_VERSION } from '../release';

type Destination = { name: string; coordinates: string; caption: string; code: string };
type JourneyEndProps = {
  destinations: Destination[];
  destination: number;
  onDestinationChange: (destination: number) => void;
};
const atlasViews = ['san-francisco', 'tokyo', 'paris'];

export default function JourneyEnd({ destinations, destination, onDestinationChange }: JourneyEndProps) {
  const place = destinations[destination];
  return <section className="journey-end" data-scroll-scene="closing" aria-labelledby="closing-title">
      <svg className="end-contours" viewBox="0 0 1200 760" fill="none" aria-hidden="true" preserveAspectRatio="xMidYMid slice">
        {Array.from({ length: 8 }, (_, index) => <ellipse key={index} cx="600" cy="400" rx={250 + index * 77} ry={110 + index * 54} transform="rotate(-28 600 400)"/>)}
      </svg>
      <div className="end-heading-row wrap"><span className="eyebrow"><span className="status-dot"/> YOUR NEXT CHAPTER</span><span className="end-top-note">NO TICKET REQUIRED.</span></div>
      <div className="end-layout wrap">
        <div className="end-copy"><h2 id="closing-title">Where to<br/><em>next?</em><span className="end-star" aria-hidden="true">✳</span></h2><p>A familiar corner. A completely new world.<br/>Your next place is only a pin away.</p><a className="button button-primary" href="#editions">Get the free desktop app <ArrowUpRight size={19}/></a><span className="end-platform"><Monitor size={13}/> Your Mac. Your iPhone. Your move.</span></div>
        <div className="end-destination">
          <div className="end-ticket">
            <div className="end-ticket-top"><span><Ghost size={19} strokeWidth={1.5}/> estera.</span><Compass size={21} strokeWidth={1.1}/></div>
            <div className="end-ticket-body"><span className="end-ticket-label">A DIFFERENT POINT OF VIEW</span><div className="end-ticket-flight"><div><span>YOUR NEXT COORDINATES</span><strong>{place.code}</strong></div><img src={`${import.meta.env.BASE_URL}assets/atlas-${atlasViews[destination]}.svg`} alt="" width="180" height="180" loading="lazy"/></div><div className="end-ticket-place" aria-live="polite" aria-atomic="true"><h3>{place.name}</h3><p>{place.caption}</p></div><div className="end-ticket-route" aria-hidden="true"><span>HERE</span><i/><ArrowRight size={15}/><span>ANYWHERE</span></div></div>
            <div className="end-ticket-bottom"><MapPin size={16} strokeWidth={1.4}/><div><span>DESTINATION</span><strong>{place.coordinates}</strong></div><span className="end-ticket-mark" aria-hidden="true">E</span></div>
          </div>
          <div className="end-destination-select"><span>PICK A POSSIBILITY</span><div role="group" aria-label="Choose a destination for your next chapter">{destinations.map((item, index) => <button key={item.code} type="button" aria-pressed={destination === index} aria-label={item.name} onClick={() => onDestinationChange(index)}><span/>{item.code}<ArrowUpRight size={12}/></button>)}</div></div>
        </div>
      </div>
      <div className="end-bottom-row wrap"><span>YOU DON'T HAVE TO LEAVE TO GO SOMEWHERE.</span><a href="#atlas-hero">One more look around <ArrowUpRight size={14}/></a></div>
    </section>;
}

export function AtlasFooter({ onRelease }: { onRelease: () => void }) {
  return <footer className="atlas-footer"><div className="wrap">
      <div className="footer-directory">
        <div className="footer-identity"><a className="brand" href="#top" aria-label="Estera home"><Ghost size={29} strokeWidth={1.6}/><span>estera<span className="brand-period">.</span></span></a><p>A little more freedom<br/>to be somewhere else.</p><a href={RELEASE_URL} className="footer-release-note"><span className="status-dot"/> v{RELEASE_VERSION} · macOS prerelease</a></div>
        <nav className="footer-link-column" aria-label="Explore Estera"><h2>EXPLORE</h2><a href="#experience">The experience</a><a href="#preview">Take a test drive</a><a href="#atlas-hero">Explore the globe <ArrowUpRight size={13}/></a></nav>
        <nav className="footer-link-column" aria-label="Download and support Estera"><h2>GET ESTERA</h2><button type="button" onClick={onRelease}>Download for Mac <ArrowUpRight size={13}/></button><a href={GITHUB_URL}>Star on GitHub <ArrowUpRight size={13}/></a><a href={RELEASE_URL}>Release notes</a><a href={`${GITHUB_URL}/blob/main/LICENSE`}>Open-source license</a></nav>
        <div className="footer-link-column footer-made-for"><h2>GOOD TO KNOW</h2><a href="#questions">Questions & answers</a><p>Made for Mac.<br/>Connected to your iPhone.</p><a className="footer-back" href="#top">Back to the top <ArrowUpRight size={14}/></a></div>
      </div>
      <div className="footer-wordmark" aria-hidden="true">estera<span>.</span><span className="footer-wordmark-symbol"><Ghost strokeWidth={.8}/></span></div>
      <div className="footer-colophon"><span>© {new Date().getFullYear()} Estera</span><span>BE HERE. GO ANYWHERE.</span><span><span className="footer-cross">+</span> A DIFFERENT POINT OF VIEW.</span></div>
    </div></footer>;
}
