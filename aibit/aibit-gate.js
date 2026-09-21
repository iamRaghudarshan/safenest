/* AI BIT download gate — served same-origin so it runs under the storefront's
   Content-Security-Policy (script-src 'self'), which blocks inline scripts.
   The hidden footer dot (class "dl-trigger") opens a password box; the correct
   password downloads the APK directly. Client-side only — a private personal
   build, not a security boundary. */
(function () {
  var APK = "/ai-bit-2.24.0.apk";
  var FILENAME = "ai-bit-2.24.0.apk";
  var PW = "10001";

  function el(id) { return document.getElementById(id); }

  function open() {
    var g = el("aibit-gate"); if (!g) return;
    g.classList.add("show");
    setTimeout(function () { var p = el("aibit-pw"); if (p) p.focus(); }, 60);
  }
  function close() {
    var g = el("aibit-gate"); if (!g) return;
    g.classList.remove("show");
    var e = el("aibit-err"); if (e) e.textContent = "";
    var p = el("aibit-pw"); if (p) p.value = "";
  }
  function download() {
    var a = document.createElement("a");
    a.href = APK; a.download = FILENAME;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
  }
  function submit() {
    var p = el("aibit-pw"), e = el("aibit-err");
    if (p && p.value.trim() === PW) { close(); download(); }
    else if (e) { e.textContent = "Incorrect password. Try again."; if (p) { p.focus(); p.select(); } }
  }
  function init() {
    var t = document.querySelectorAll(".dl-trigger");
    for (var i = 0; i < t.length; i++) {
      t[i].addEventListener("click", function (ev) { ev.preventDefault(); open(); });
    }
    var ok = el("aibit-ok"); if (ok) ok.addEventListener("click", submit);
    var x = el("aibit-x"); if (x) x.addEventListener("click", close);
    var g = el("aibit-gate"); if (g) g.addEventListener("click", function (ev) { if (ev.target === g) close(); });
    var p = el("aibit-pw"); if (p) p.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") submit(); if (ev.key === "Escape") close();
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
