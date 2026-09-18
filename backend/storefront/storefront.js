// Storefront behaviour. Served as an external file (not inline) because the app
// ships a strict Content-Security-Policy — script-src 'self' — that blocks inline
// <script> and inline on* handlers. Same origin, so this loads and runs.

const reduceMotion = window.matchMedia
  && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// ------------------------------------------------------------ mobile menu
// The links are a panel below 900px. Three things make it behave like a menu
// rather than a div that moves: aria-expanded so it is announced, Escape and
// outside-click to close, and focus returning to the button afterwards so a
// keyboard user is not dumped at the top of the document.
const navToggle = document.getElementById('nav-toggle');
const navLinks = document.getElementById('nav-links');
if (navToggle && navLinks) {
  const mq = window.matchMedia('(max-width: 900px)');

  const setOpen = (open) => {
    navLinks.classList.toggle('open', open);
    navToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    navToggle.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
  };

  // Collapsed links must leave the tab order entirely. A max-height of 0 still
  // leaves them focusable, so a keyboard user tabs into six invisible links.
  const syncHidden = () => {
    if (mq.matches && !navLinks.classList.contains('open')) navLinks.hidden = true;
    else navLinks.hidden = false;
  };

  navToggle.addEventListener('click', () => {
    const open = navToggle.getAttribute('aria-expanded') === 'true';
    if (!open) { navLinks.hidden = false; requestAnimationFrame(() => setOpen(true)); }
    else { setOpen(false); setTimeout(syncHidden, 300); }
  });

  // Following a link should close the menu — every link here is an in-page
  // anchor, so without this the panel stays over the thing it scrolled to.
  navLinks.addEventListener('click', (e) => {
    if (e.target.closest('a') && mq.matches) {
      setOpen(false); setTimeout(syncHidden, 300);
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && navToggle.getAttribute('aria-expanded') === 'true') {
      setOpen(false); setTimeout(syncHidden, 300); navToggle.focus();
    }
  });

  document.addEventListener('click', (e) => {
    if (!mq.matches) return;
    if (navToggle.getAttribute('aria-expanded') !== 'true') return;
    if (e.target.closest('#nav-links') || e.target.closest('#nav-toggle')) return;
    setOpen(false); setTimeout(syncHidden, 300);
  });

  // Resizing past the breakpoint with the panel open would otherwise leave the
  // desktop bar in a half-open state.
  mq.addEventListener('change', () => { setOpen(false); syncHidden(); });
  syncHidden();
}

// Logo fallbacks (were inline onerror attributes, which CSP also blocks).
document.querySelectorAll('img.logo-img').forEach((img) => {
  img.addEventListener('error', () => { img.style.display = 'none'; });
});

// Mark the page script-capable so the reveal animation applies; without JS the
// content simply shows (the CSS hiding is gated on this class).
document.documentElement.classList.add('js');

// Each module gets its own colour so the grid reads as vivid rather than a wall
// of blue. The colour drives the icon chip, the top bar and the hover glow.
// Each row is [icon, name, line, colour, span]. `span` is how many columns the
// card takes on a four-column grid — the bento rhythm, and it is deliberate:
// Expenses, Documents, Photos and Vault are what sell this product, so they are
// what gets the room. The spans must still total a multiple of four per row or
// the grid grows a ragged edge.
const MODULES = [
  ['\u{1F4B0}', 'Expenses', 'Track what you spend, month by month \u2014 grouped, searchable, and totalled without you doing a thing.', '#1656C6', 2],
  ['\u{1F5BC}\uFE0F', 'Photos', 'Your whole phone library, on your own machine.', '#6366f1', 1],
  ['\u{1F510}', 'Vault', 'Passwords and secrets, AES-256 encrypted.', '#0891b2', 1],

  ['\u{1F3E6}', 'Loans', 'Every loan and what is left to pay.', '#7b3ff2', 1],
  ['\u{1F4B3}', 'Cards', 'Statements, and what is due when.', '#0ea5e9', 1],
  ['\u{1F4C4}', 'Documents', 'Scan and keep every important paper \u2014 Aadhaar, deeds, warranties \u2014 found by searching inside them.', '#f43f5e', 2],

  ['\u{1F6E1}\uFE0F', 'Insurance', 'Policies, premiums, renewal dates.', '#10b981', 1],
  ['\u{1F4C8}', 'Investments', 'What you hold, and what it is worth.', '#f59e0b', 1],
  ['\u{1F514}', 'Reminders', 'Never miss a bill or a renewal.', '#e11d48', 1],
  ['\u2705', 'To-dos', 'The little things, kept with the rest.', '#059669', 1],

  ['\u{1F4C5}', 'Habits', 'The things you mean to do daily, tracked as a streak you can actually see.', '#8b5cf6', 2],
  ['\u{1F4DD}', 'Notes', 'Anything that does not fit a form, filed with everything that does.', '#14b8a6', 2],
];

// What the app does ACROSS the modules. Every line here was checked against the
// code before it was written, because a storefront is a promise: two-factor
// sign-in is deliberately absent even though auth.py has six routes for it, as
// nothing in the frontend calls any of them.
const EXTRAS = [
  ['\u{1F4F4}', 'Works offline',
   'Install it on your phone or desktop and it opens without a connection.', '#0891b2'],
  ['\u{1F642}', 'Knows who and what is in your photos',
   'Pictures group themselves by face, and you can search by what a photo shows.', '#6366f1'],
  ['\u{1F9F9}', 'Clears out duplicates',
   'Finds exact copies and near ones \u2014 resized, re-saved, edited \u2014 and gives the space back.', '#f59e0b'],
  ['\u{1F514}', 'One summary a day',
   'Bills, renewals and tasks in a single notification, at a time you choose.', '#e11d48'],
  ['\u{1F46A}', 'A sign-in for everyone at home',
   'Each person keeps their own records. Nobody sees anybody else\u2019s.', '#10b981'],
  ['\u{1F4F2}', 'Backs up an iPhone\u2019s whole library',
   'Straight from the phone\u2019s Shortcuts app \u2014 no cable, no browser upload.', '#7b3ff2'],
  ['\u{1F310}', 'Your own web address',
   'Reach your records from outside the house, on a domain that is yours.', '#0ea5e9'],
  ['\u{1F4E6}', 'Moves house in one click',
   'Take the lot \u2014 records, photos, settings \u2014 to a new computer on a USB drive.', '#f43f5e'],
];
const card = ([i, t, d, c, span], n) =>
  `<div class="feat reveal${span === 2 ? ' wide' : ''}" style="--fc:${c};transition-delay:${n * 45}ms">
     <div class="ic">${i}</div><h3>${t}</h3><p>${d}</p></div>`;

// The screenshot strip. Built here rather than written as markup so the tabs and
// the images cannot drift apart, and so only the first image is fetched eagerly —
// the rest load when someone actually asks for them.
const SCREENS = [
  ['Dashboard', 'dashboard', 'Everything due, the moment you open it'],
  ['Photos', 'gallery', 'Your whole library — searchable by who and what is in it'],
  ['Documents', 'documents', 'Scanned, sorted, and found by searching inside them'],
  ['Vault', 'vault', 'Passwords and secrets, sealed with AES-256'],
  ['Expenses', 'expenses', 'What you spent, grouped by day'],
  ['Investments', 'investments', 'What you hold, and what it is worth now'],
  ['Insurance', 'insurance', 'Policies, premiums and renewal dates'],
];
const tabs = document.getElementById('shot-tabs');
const stage = document.getElementById('shot-stage');
if (tabs && stage) {
  // ONE image whose src changes, not four stacked with `hidden` on three of them.
  // That was the first attempt and it silently never loaded anything: a
  // display:none image with loading="lazy" is never fetched, and un-hiding it did
  // not reliably start the fetch either — so three of the four tabs showed
  // nothing at all, with no console error to give it away.
  // Dimensions on the element as well, so the box is right even before the
  // stylesheet's aspect-ratio applies. Lazy because the section is well below
  // the fold on every viewport.
  stage.innerHTML =
    '<img alt="" decoding="async" loading="lazy" width="2000" height="1344">';
  const img = stage.querySelector('img');
  tabs.innerHTML = SCREENS.map(([label], i) =>
    `<button type="button" class="shot-tab${i ? '' : ' on'}" data-i="${i}">${label}</button>`).join('');
  const btns = [...tabs.querySelectorAll('.shot-tab')];

  const show = (i) => {
    const [label, file, alt] = SCREENS[i];
    img.src = `/storefront-img/${file}.webp`;
    img.alt = `${label} — ${alt}`;
    btns.forEach((x, n) => x.classList.toggle('on', n === i));
  };
  show(0);

  tabs.addEventListener('click', (e) => {
    const b = e.target.closest('.shot-tab');
    if (b) show(+b.dataset.i);
  });
  // Fetch on hover so the swap feels instant when the click lands.
  tabs.addEventListener('pointerover', (e) => {
    const b = e.target.closest('.shot-tab');
    if (b) new Image().src = `/storefront-img/${SCREENS[+b.dataset.i][1]}.webp`;
  });
}

const grid = document.getElementById('features-grid');
if (grid) grid.innerHTML = MODULES.map(card).join('');

const extras = document.getElementById('extras-grid');
if (extras) extras.innerHTML = EXTRAS.map(card).join('');

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

  // The wordmark is split in two so it can be two-tone, and `nav-name` is only
  // the FIRST half. Writing the whole name into it rendered "SafeNestNest".
  //
  // The app is rebrandable, so the split cannot be hardcoded: take the second
  // capital letter as the seam where there is one ("SafeNest" -> Safe | Nest),
  // and otherwise put the whole name in the dark half and leave the light half
  // empty. A renamed copy then still reads correctly, just in one colour.
  const seam = name.slice(1).search(/[A-Z]/);
  const head = seam > -1 ? name.slice(0, seam + 1) : name;
  const tail = seam > -1 ? name.slice(seam + 1) : '';
  for (const id of ['nav-name', 'foot-name']) {
    const el = document.getElementById(id);
    if (!el) continue;
    el.textContent = head;
    const rest = el.parentElement && el.parentElement.querySelector('i');
    if (rest) rest.textContent = tail;
  }

  // The logo IMAGE says "SafeNest" and cannot say anything else. Only the
  // publisher's own installation serves this page, so that is normally fine —
  // but if this copy has been renamed, showing a picture of the wrong name is
  // the one outcome worth avoiding. Swap the image for the typeset wordmark.
  if (name !== 'SafeNest') {
    for (const [imgSel, wordId] of [['nav .brand-logo', 'nav-word'],
                                    ['footer .brand-logo', 'foot-word']]) {
      const img = document.querySelector(imgSel);
      const word = document.getElementById(wordId);
      if (img) img.remove();
      if (word) word.hidden = false;
    }
  }
  const card = document.getElementById('card-name');
  if (card) card.textContent = name;
  document.title = name + ' — ' + (b.tagline || 'kept safe at home');
  const fc = document.getElementById('foot-copy');
  if (fc) fc.textContent = '© ' + name + '. Licensed software. Not for resale or redistribution.';
  // The hero sub-line keeps its richer descriptive copy — the tagline is already
  // essentially the headline, so echoing it here just repeats it.
  // NOT nav-logo or foot-logo any more: those are the vector mark now, and
  // overwriting them with the raster branding icon put a blurry 192px PNG in the
  // navigation of a page that already had a crisp SVG sitting there.
  if (b.icons && b.icons['192']) {
    for (const id of ['card-logo']) {
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
      const okEl = document.getElementById('req-ok');
      okEl.style.display = 'block'; okEl.focus();
      // Downloading is gated behind requesting: the buttons live hidden in
      // #req-download and are revealed only now, so every downloader is a captured
      // request. (The buttons were populated with availability on page load.)
      const dld = document.getElementById('req-download');
      if (dld) { dld.style.display = 'block'; dld.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
    } catch (ex) {
      err.textContent = ex.message;
      btn.disabled = false; btn.textContent = 'Request a licence →';
      // role="alert" announces it, but a sighted keyboard user still has to
      // hunt for where the message appeared. Put them on it.
      err.setAttribute('tabindex', '-1'); err.focus();
    }
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
    } catch (ex) { err.textContent = ex.message; btn.disabled = false; btn.textContent = 'Send to support →';  err.setAttribute('tabindex','-1'); err.focus(); }
  });
}

// The stats show their correct final values from the HTML and animate in via the
// reveal fade below. A JS count-up was tried and removed: it can stall mid-count
// (a throttled tab, a slow frame) and show a WRONG figure like "4 modules", which
// costs exactly the confidence the numbers are there to build. A right number
// that fades in beats a wrong one that animates.

// Reveal on scroll (staggered).
// ---------------------------------------------------------------- pointer glow
// Cards light up from where the cursor actually is, not uniformly. Two custom
// properties per card, written on pointermove and read by a radial-gradient in
// the CSS. Skipped entirely under reduced-motion and on touch, where there is no
// hover to respond to and the listener would be pure battery cost.
if (!reduceMotion && window.matchMedia('(hover: hover)').matches) {
  const glow = document.querySelectorAll('.feat, .step, .check');
  glow.forEach((el) => {
    el.addEventListener('pointermove', (e) => {
      const r = el.getBoundingClientRect();
      el.style.setProperty('--mx', `${e.clientX - r.left}px`);
      el.style.setProperty('--my', `${e.clientY - r.top}px`);
    });
  });

}


// ------------------------------------------------------------------- 3D tilt
// Cards and the hero product rotate a couple of degrees towards the cursor.
// Written as an inline transform because the reveal animation owns the same
// property from a class — inline wins, and by the time anything is hovered the
// reveal has long finished.
//
// Capped at 5 degrees. Beyond that it stops reading as a surface catching the
// light and starts reading as a gimmick, and text on a steeply rotated plane is
// genuinely harder to read.
if (!reduceMotion && window.matchMedia('(hover: hover)').matches) {
  const MAX = 5;
  const tiltable = document.querySelectorAll('.feat, .step, .check');

  tiltable.forEach((el) => {
    el.addEventListener('pointermove', (e) => {
      const r = el.getBoundingClientRect();
      const px = (e.clientX - r.left) / r.width - 0.5;
      const py = (e.clientY - r.top) / r.height - 0.5;
      el.style.transform =
        `rotateY(${px * MAX}deg) rotateX(${-py * MAX}deg) translateY(-3px)`;
    });
    el.addEventListener('pointerleave', () => { el.style.transform = ''; });
  });

  // The hero product tilts with the pointer AND drifts with the scroll, so both
  // have to be composed in one place — two listeners each writing
  // `style.transform` would simply overwrite each other, and the bug looks like
  // "the parallax stopped working" rather than anything to do with tilt.
  const laptop = document.querySelector('.shot-laptop');
  const hero = document.querySelector('header.hero');
  if (laptop && hero) {
    let rx = 0, ry = 0, drift = 0, queued = false, onScreen = true;

    const paint = () => {
      queued = false;
      laptop.style.transform =
        `translate3d(0, ${drift}px, 0) rotateY(${ry}deg) rotateX(${rx}deg)`;
    };
    const schedule = () => {
      if (!queued) { queued = true; requestAnimationFrame(paint); }
    };

    new IntersectionObserver(([x]) => { onScreen = x.isIntersecting; }).observe(hero);

    hero.addEventListener('pointermove', (e) => {
      const r = hero.getBoundingClientRect();
      ry = ((e.clientX - r.left) / r.width - 0.5) * 7;
      rx = -((e.clientY - r.top) / r.height - 0.5) * 5;
      schedule();
    });
    hero.addEventListener('pointerleave', () => { rx = 0; ry = 0; schedule(); });
    addEventListener('scroll', () => {
      if (!onScreen) return;
      drift = Math.min(window.scrollY, 700) * -0.055;
      schedule();
    }, { passive: true });
  }
}

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
