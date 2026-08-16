// Storefront behaviour. Served as an external file (not inline) because the app
// ships a strict Content-Security-Policy — script-src 'self' — that blocks inline
// <script> and inline on* handlers. Same origin, so this loads and runs.

const reduceMotion = window.matchMedia
  && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// Logo fallbacks (were inline onerror attributes, which CSP also blocks).
document.querySelectorAll('img.logo-img').forEach((img) => {
  img.addEventListener('error', () => { img.style.display = 'none'; });
});

// Mark the page script-capable so the reveal animation applies; without JS the
// content simply shows (the CSS hiding is gated on this class).
document.documentElement.classList.add('js');

// Each module gets its own colour so the grid reads as vivid rather than a wall
// of blue. The colour drives the icon chip, the top bar and the hover glow.
const MODULES = [
  ['\u{1F4B0}', 'Expenses', 'Track what you spend, month by month.', '#1656C6'],
  ['\u{1F3E6}', 'Loans', 'Every loan, its schedule and what’s left to pay.', '#7b3ff2'],
  ['\u{1F4B3}', 'Cards', 'Cards, statements and what’s due when.', '#0ea5e9'],
  ['\u{1F6E1}️', 'Insurance', 'Policies, premiums and renewal dates in one view.', '#10b981'],
  ['\u{1F4C8}', 'Investments', 'What you hold, all in one place.', '#f59e0b'],
  ['\u{1F4C4}', 'Documents', 'Scan and keep every important paper, searchable.', '#f43f5e'],
  ['\u{1F5BC}️', 'Photos', 'Back up your whole phone library to your own machine.', '#6366f1'],
  ['\u{1F510}', 'Vault', 'Passwords and secrets, AES-256 encrypted.', '#0891b2'],
  ['\u{1F514}', 'Reminders', 'Never miss a bill, renewal or task.', '#e11d48'],
  ['✅', 'To-dos', 'The little things, kept with everything else.', '#059669'],
];
const grid = document.getElementById('features-grid');
if (grid) {
  grid.innerHTML = MODULES.map(([i, t, d, c], n) =>
    `<div class="feat reveal" style="--fc:${c};transition-delay:${n * 60}ms">
       <div class="ic">${i}</div><h3>${t}</h3><p>${d}</p></div>`).join('');
}

const fmtSize = (b) => b >= 1e9 ? (b / 1e9).toFixed(1) + ' GB'
  : b >= 1e6 ? Math.round(b / 1e6) + ' MB' : Math.round(b / 1e3) + ' KB';

// Brand: name, tagline, colour and logo all come live from the server, so the
// site always matches whatever the app is branded as.
fetch('/api/branding').then((r) => r.json()).then((b) => {
  if (b.theme_color) {
    document.documentElement.style.setProperty('--brand', b.theme_color);
    const tc = document.getElementById('theme-color');
    if (tc) tc.setAttribute('content', b.theme_color);
  }
  const name = b.app_name || 'SafeNest';
  for (const id of ['nav-name', 'card-name', 'foot-name']) {
    const el = document.getElementById(id); if (el) el.textContent = name;
  }
  document.title = name + ' — ' + (b.tagline || 'kept safe at home');
  const fc = document.getElementById('foot-copy');
  if (fc) fc.textContent = '© ' + name + '. Licensed software. Not for resale or redistribution.';
  // The hero sub-line keeps its richer descriptive copy — the tagline is already
  // essentially the headline, so echoing it here just repeats it.
  if (b.icons && b.icons['192']) {
    for (const id of ['nav-logo', 'card-logo', 'foot-logo']) {
      const el = document.getElementById(id); if (el) { el.src = b.icons['192']; el.style.display = ''; }
    }
  }
}).catch(() => {});

fetch('/api/public/download/meta').then((r) => r.json()).then((m) => {
  const p = m.platforms || {}, bits = [];
  for (const [plat, info] of Object.entries(p)) {
    const btn = document.getElementById('dl-' + plat);
    if (!btn) continue;
    const nm = plat[0].toUpperCase() + plat.slice(1);
    if (info.available) {
      bits.push(nm + ' ' + (info.version || '') + (info.size_bytes ? ' · ' + fmtSize(info.size_bytes) : ''));
    } else {
      btn.setAttribute('disabled', 'disabled'); btn.removeAttribute('href');
      btn.innerHTML = '⬇  ' + nm + ' (soon)';
    }
  }
  const meta = document.getElementById('dl-meta');
  if (meta) meta.textContent = bits.length ? 'Latest: ' + bits.join('   ·   ') : 'No downloads published yet.';
}).catch(() => { const meta = document.getElementById('dl-meta'); if (meta) meta.textContent = ''; });

const reqForm = document.getElementById('req');
if (reqForm) {
  reqForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = document.getElementById('req-btn'), err = document.getElementById('req-err');
    err.textContent = ''; btn.disabled = true; btn.textContent = 'Sending…';
    try {
      const res = await fetch('/api/public/licence-request', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: document.getElementById('rn').value.trim(),
          email: document.getElementById('re').value.trim(),
          message: document.getElementById('rm').value.trim(),
        }),
      });
      if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || 'Something went wrong.'); }
      reqForm.style.display = 'none';
      document.getElementById('req-ok').style.display = 'block';
      // Downloading is gated behind requesting: the buttons live hidden in
      // #req-download and are revealed only now, so every downloader is a captured
      // request. (The buttons were populated with availability on page load.)
      const dld = document.getElementById('req-download');
      if (dld) { dld.style.display = 'block'; dld.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
    } catch (ex) { err.textContent = ex.message; btn.disabled = false; btn.textContent = 'Request a licence →'; }
  });
}

// The Mac one-line installer, built from wherever the page is served so it always
// points at the right host. curl-fetched files are not quarantined, which is the
// whole reason this avoids the "damaged" Gatekeeper block on un-notarised apps.
const macCmdEl = document.getElementById('mac-cmd');
const MAC_CMD = `curl -fsSL ${location.origin}/install-mac.sh | bash`;
if (macCmdEl) macCmdEl.textContent = MAC_CMD;
const macCopy = document.getElementById('mac-copy');
if (macCopy) {
  macCopy.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(MAC_CMD); }
    catch { const r = document.createRange(); r.selectNode(macCmdEl); const s = getSelection(); s.removeAllRanges(); s.addRange(r); }
    const was = macCopy.textContent; macCopy.textContent = 'Copied ✓';
    setTimeout(() => { macCopy.textContent = was; }, 1600);
  });
}

const supForm = document.getElementById('sup');
if (supForm) {
  supForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = document.getElementById('sup-btn'), err = document.getElementById('sup-err');
    err.textContent = ''; btn.disabled = true; btn.textContent = 'Sending…';
    try {
      const res = await fetch('/api/public/support', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: document.getElementById('sn').value.trim(),
          email: document.getElementById('se').value.trim(),
          licence_key: document.getElementById('sk').value.trim(),
          subject: document.getElementById('ss').value.trim(),
          body: document.getElementById('sb').value.trim(),
        }),
      });
      if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || 'Something went wrong.'); }
      supForm.style.display = 'none';
      document.getElementById('sup-ok').style.display = 'block';
    } catch (ex) { err.textContent = ex.message; btn.disabled = false; btn.textContent = 'Send to support →'; }
  });
}

// The stats show their correct final values from the HTML and animate in via the
// reveal fade below. A JS count-up was tried and removed: it can stall mid-count
// (a throttled tab, a slow frame) and show a WRONG figure like "4 modules", which
// costs exactly the confidence the numbers are there to build. A right number
// that fades in beats a wrong one that animates.

// Reveal on scroll (staggered).
const io = new IntersectionObserver((es) => es.forEach((x) => {
  if (!x.isIntersecting) return;
  x.target.classList.add('in');
  io.unobserve(x.target);
}), { threshold: .18 });
document.querySelectorAll('.reveal').forEach((el) => io.observe(el));
// Safety net: reveal anything the observer missed so nothing stays invisible.
// Kept short — a marketing page must never sit blank waiting on an observer.
setTimeout(() => document.querySelectorAll('.reveal:not(.in)').forEach((el) => el.classList.add('in')), 800);

// Slim scroll-progress bar along the top.
const bar = document.getElementById('progress');
if (bar) {
  const onScroll = () => {
    const h = document.documentElement;
    const max = h.scrollHeight - h.clientHeight;
    bar.style.width = (max > 0 ? (h.scrollTop / max) * 100 : 0) + '%';
  };
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();
}
