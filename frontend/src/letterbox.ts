// Detects the iOS standalone letterbox and adapts what legitimately can adapt.
//
// On an affected home-screen install, iOS sizes the layout viewport short of the
// screen and paints the leftover bottom strip with the page's background colour —
// but never composites content there. Shifting the tab bar down into it makes the
// icons disappear (verified on device), so no layout is moved into the strip.
//
// What CAN change: on such an install the home indicator lives in the strip, not
// over the bar, so the bar doesn't need to reserve indicator clearance — its
// bottom padding drops to a plain 6px, putting the icons on the lowest line iOS
// will actually render.
export function initLetterboxCompensation(): void {
  const root = document.documentElement
  // Neutralise the variable in case a stale cached stylesheet still references it.
  root.style.setProperty('--vp-gap', '0px')

  const standalone = window.matchMedia('(display-mode: standalone)').matches
    || (navigator as unknown as { standalone?: boolean }).standalone === true
  if (!standalone) return

  const measure = () => {
    if (!document.body) return
    const probe = document.createElement('div')
    probe.style.cssText =
      'position:fixed;top:0;left:0;width:1px;height:100vh;' +
      'padding-top:env(safe-area-inset-top,0px);visibility:hidden;pointer-events:none;'
    document.body.appendChild(probe)
    const fullH = probe.getBoundingClientRect().height
    const safeTop = parseFloat(getComputedStyle(probe).paddingTop) || 0
    probe.remove()
    const letterboxed = safeTop > 0 && fullH - root.clientHeight > 20
    if (letterboxed) root.style.setProperty('--bar-pad-bottom', '6px')
    else root.style.removeProperty('--bar-pad-bottom')
  }

  measure()
  window.setTimeout(measure, 300)
  window.setTimeout(measure, 900)
  window.addEventListener('orientationchange', () => {
    window.setTimeout(measure, 150)
    window.setTimeout(measure, 600)
  })
}
