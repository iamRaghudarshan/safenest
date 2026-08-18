/** A full, friendly nature world fixed behind the WHOLE app — every screen and
 *  the sign-in page: sky, clouds, birds, a sun, snow-capped mountains, a pine
 *  forest, rolling hills, a deer and a rabbit. Kept faint (via --nb-op) so cards
 *  and text stay readable on top. Colours are CSS variables, so it turns to dusk
 *  in dark mode and on login without a second copy. Mounted once in main.tsx. */
export function NatureBackdrop() {
  return (
    <div className="nature-backdrop" aria-hidden="true">
      <svg className="nb-scene" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice">
        {/* sun with a soft halo, top-right */}
        <circle className="nb-sun" cx="1210" cy="150" r="110" opacity="0.18" />
        <circle className="nb-sun" cx="1210" cy="150" r="66" />

        {/* drifting clouds */}
        <g className="nb-cloud nb-drift1" fill="var(--nb-cloud)">
          <ellipse cx="300" cy="150" rx="95" ry="40" /><ellipse cx="380" cy="132" rx="64" ry="38" /><ellipse cx="228" cy="138" rx="52" ry="32" />
        </g>
        <g className="nb-cloud nb-drift2" fill="var(--nb-cloud)" opacity="0.8">
          <ellipse cx="820" cy="120" rx="72" ry="30" /><ellipse cx="884" cy="104" rx="46" ry="26" />
        </g>
        <g className="nb-cloud nb-drift1" fill="var(--nb-cloud)" opacity="0.7">
          <ellipse cx="1080" cy="240" rx="60" ry="24" /><ellipse cx="1128" cy="228" rx="40" ry="20" />
        </g>

        {/* a flock of birds */}
        <g stroke="var(--nb-bird)" strokeWidth="4" strokeLinecap="round" fill="none" opacity="0.55">
          <path d="M520 210 q18 -18 36 0 q18 -18 36 0" /><path d="M600 186 q14 -14 28 0 q14 -14 28 0" /><path d="M470 196 q12 -12 24 0 q12 -12 24 0" />
        </g>

        {/* snow-capped mountains */}
        <path className="nb-far" d="M60 560 L360 250 L660 560 Z" />
        <path className="nb-snow" d="M360 250 L432 322 Q396 296 360 322 Q324 296 288 322 Z" />
        <path className="nb-far2" d="M540 560 L900 200 L1260 560 Z" />
        <path className="nb-snow" d="M900 200 L984 290 Q942 262 900 290 Q858 262 816 290 Z" />

        {/* rolling hills */}
        <path className="nb-h1" d="M0 560 Q360 496 720 548 T1440 532 V900 H0 Z" />
        <path className="nb-h2" d="M0 630 Q360 582 720 620 T1440 606 V900 H0 Z" />
        <path className="nb-h3" d="M0 706 Q360 672 720 706 T1440 690 V900 H0 Z" />

        {/* a pine forest scattered over the hills */}
        {[[210, 636, 1.1], [330, 664, 0.9], [1180, 660, 1.1], [1060, 690, 0.95], [980, 648, 0.8], [560, 690, 0.85]].map(([x, y, s], i) => (
          <g key={i} className="nb-pineg" transform={`translate(${x} ${y}) scale(${s})`}>
            <rect x="-6" y="0" width="12" height="30" fill="var(--nb-trunk)" />
            <path d="M0 -84 L34 -24 L-34 -24 Z" fill="var(--nb-pine)" />
            <path d="M0 -60 L40 12 L-40 12 Z" fill="var(--nb-pine2)" />
          </g>
        ))}

        {/* a deer grazing on a hill */}
        <g transform="translate(748 664)" fill="var(--nb-deer)">
          <ellipse cx="0" cy="-26" rx="30" ry="16" />
          <rect x="-24" y="-18" width="7" height="28" rx="3" /><rect x="-9" y="-18" width="7" height="28" rx="3" />
          <rect x="12" y="-18" width="7" height="28" rx="3" /><rect x="24" y="-18" width="7" height="28" rx="3" />
          <path d="M27 -36 Q45 -39 45 -62 L54 -62 Q57 -36 36 -24 Z" />
          <circle cx="51" cy="-66" r="9.5" />
          <path d="M45 -75 l-6 -18 M57 -75 l6 -18" stroke="var(--nb-deer)" strokeWidth="4.5" strokeLinecap="round" />
        </g>

        {/* a rabbit in the foreground */}
        <g transform="translate(300 786)" fill="var(--nb-rabbit)">
          <ellipse cx="0" cy="-15" rx="20" ry="17" />
          <circle cx="18" cy="-28" r="11" />
          <ellipse cx="15" cy="-46" rx="4.5" ry="13" /><ellipse cx="26" cy="-46" rx="4.5" ry="13" />
          <circle cx="-18" cy="-9" r="7" fill="var(--nb-cloud)" />
        </g>

        {/* wildflowers */}
        {[[160, 740], [520, 770], [900, 748], [1240, 762]].map(([x, y], i) => (
          <g key={i} transform={`translate(${x} ${y})`}>
            <line x1="0" y1="0" x2="0" y2="30" stroke="var(--nb-pine2)" strokeWidth="5" />
            <circle cx="0" cy="-6" r="10" fill={i % 2 ? 'var(--nb-petal2)' : 'var(--nb-petal)'} />
            <circle cx="0" cy="-6" r="4" fill="#fff" opacity="0.85" />
          </g>
        ))}
      </svg>
    </div>
  )
}
