#!/usr/bin/env python3
"""
JSS Audio Library — one-pass patcher.

Fixes, across every HTML file in the repo:

  Audio reliability
    * chunked speech in the AV magazines (they had none)
    * transient TTS errors skip ahead instead of killing the session
    * screen-lock pauses and resumes instead of stopping dead
    * playback position saved in the magazines too

  State
    * resume keys namespaced per book (they were global and collided)
    * one storage namespace instead of three
    * the resume banner that every file looked up but none contained

  Player UI
    * bottom-left zoom button reads "Zoom" in English
    * dark-mode button carries a Dark/Light label
    * speed is a slider in the player, not a cycling button

  Cross-browser
    * Chrome, Edge and Firefox all get the same custom player

  Reach
    * mediaSession lock-screen controls, neural voices preferred
    * per-page og:url / og:image / canonical, OG tags for the magazines
    * manifest linked from book pages, offline fonts, capped SW cache
    * real book and page totals baked into index.html

Usage:
    python tools/jss_patch.py --dry-run --diff   # show changes, write nothing
    python tools/jss_patch.py                    # apply
    python tools/jss_patch.py --report           # inventory only

Safe to run twice: every insertion is marker-guarded.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
import urllib.parse
from pathlib import Path

BASE_URL = "https://kamleshmehta-kp.github.io/JSS-Audio-Project/"
SHARE_IMAGE = "icon-512.png"

MARK = "jss-patch-v1"
MARK_OPEN = f"<!-- {MARK}:start -->"
JS_MARK = f"/* {MARK} */"

BOOK_DIRS = (
    "AkramVignan-Gujarati",
    "AkramVignan-Hindi",
    "JSSBooks-Gujarati",
    "JSSBooks-Hindi",
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def rel_url(root: Path, path: Path) -> str:
    rel = path.relative_to(root).as_posix()
    return BASE_URL + urllib.parse.quote(rel)


def book_id(root: Path, path: Path) -> str:
    """Stable per-book key. Survives re-uploads of the same book."""
    rel = path.relative_to(root).as_posix()
    rel = re.sub(r"\.html?$", "", rel, flags=re.I)
    rel = re.sub(r"[_\- ]?(Ver|ver|V)[_\- ]?\d+([._]\d+)*", "", rel)
    rel = re.sub(r"[_\- ]?\d{6,8}$", "", rel)
    return re.sub(r"[^A-Za-z0-9]+", "-", rel).strip("-").lower()


def page_title(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else "JSS Audio Library"


def is_av_engine(html: str) -> bool:
    """The magazines run an older fork with its own page markup.

    Detected by DOM shape rather than by a missing function, so the check
    still works after the patch has added the missing functions.
    """
    # Must match real magazine markup, not the string as it appears inside
    # injected JavaScript selectors.
    return 'class="av-page-wrapper' in html


def js_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# --------------------------------------------------------------------------
# 1. storage namespacing
# --------------------------------------------------------------------------

PER_BOOK_KEYS = {
    "pd_audio_index": "audioIndex",
    "jss_audio_index": "audioIndex",
    "pd_audio_chunk": "audioChunk",
    "jss_audio_chunk": "audioChunk",
    "pd_audio_boundary": "audioBoundary",
    "jss_audio_boundary": "audioBoundary",
}

GLOBAL_KEYS = {
    "theme": "theme",
    "avTheme": "theme",
    "fontIdx": "zoomIdx",
    "avFontIdx": "zoomIdx",
    "rateIndex": "rate",
    "avSpeedIndex": "rate",
}


def patch_storage_keys(html: str) -> tuple[str, int]:
    """Route every storage call through the namespacing helpers."""
    pattern = re.compile(
        r"(?P<store>localStorage|sessionStorage)\.(?P<op>getItem|setItem|removeItem)"
        r"\(\s*'(?P<key>[A-Za-z_][A-Za-z0-9_]*)'"
    )
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        key = m.group("key")
        if key in PER_BOOK_KEYS:
            new = f"JSSK('{PER_BOOK_KEYS[key]}')"
        elif key in GLOBAL_KEYS:
            new = f"JSSG('{GLOBAL_KEYS[key]}')"
        else:
            return m.group(0)
        count += 1
        return f"{m.group('store')}.{m.group('op')}({new}"

    return pattern.sub(repl, html), count


# --------------------------------------------------------------------------
# 2. JS preamble (identity + helpers)
# --------------------------------------------------------------------------

def js_preamble(bid: str, title: str, lang: str) -> str:
    return f"""{JS_MARK}
// ---- identity + storage namespacing -------------------------------------
const JSS_BOOK_ID = {js_str(bid)};
const JSS_BOOK_TITLE = {js_str(title)};
const JSS_LANG = {js_str(lang)};
function JSSK(n) {{ return 'jss:' + JSS_BOOK_ID + ':' + n; }}
function JSSG(n) {{ return 'jss:' + n; }}
function jssGet(k) {{
  try {{ return localStorage.getItem(k); }} catch (e) {{}}
  try {{ return sessionStorage.getItem(k); }} catch (e) {{}}
  return null;
}}
// One-time migration off the old global keys so existing readers keep settings.
(function () {{
  try {{
    if (localStorage.getItem('jss:migrated') === '1') return;
    ['theme', 'avTheme'].forEach(function (o) {{
      var v = localStorage.getItem(o);
      if (v && !localStorage.getItem(JSSG('theme'))) localStorage.setItem(JSSG('theme'), v);
    }});
    ['fontIdx', 'avFontIdx'].forEach(function (o) {{
      var v = localStorage.getItem(o);
      if (v && !localStorage.getItem(JSSG('zoomIdx'))) localStorage.setItem(JSSG('zoomIdx'), v);
    }});
    ['rateIndex', 'avSpeedIndex'].forEach(function (o) {{
      var v = localStorage.getItem(o);
      if (v && !localStorage.getItem(JSSG('rate'))) localStorage.setItem(JSSG('rate'), v);
    }});
    localStorage.setItem('jss:migrated', '1');
  }} catch (e) {{}}
}})();
"""


# --------------------------------------------------------------------------
# 3. cross-browser unification
# --------------------------------------------------------------------------

EDGE_DECL = re.compile(
    r"const\s+isEdge\s*=\s*/Edg\|Edge\|EdgiOS\|EdgA/i\.test\(navigator\.userAgent\)"
    r"(?:\s*&&\s*!isIOS)?\s*;"
)

EDGE_REPLACEMENT = """const isEdgeUA = /Edg|Edge|EdgiOS|EdgA/i.test(navigator.userAgent);
const isFirefox = /Firefox/i.test(navigator.userAgent);
const hasTTS = ('speechSynthesis' in window);
/* Unified experience: Chrome, Edge and Firefox all use the built-in player.
   Edge's native Read Aloud is no longer required, so every isEdge branch
   below now takes the standard path. isEdgeUA remains for banner copy. */
const isEdge = false;"""


def patch_cross_browser(html: str) -> tuple[str, bool]:
    if "const isEdgeUA" in html:
        return html, False
    new, n = EDGE_DECL.subn(EDGE_REPLACEMENT, html, count=1)
    return new, bool(n)


# --------------------------------------------------------------------------
# 4. AV magazine engine repairs
# --------------------------------------------------------------------------

AV_VISIBILITY = re.compile(
    r"document\.addEventListener\(\s*'visibilitychange'\s*,\s*\(\)\s*=>\s*\{\s*"
    r"if\s*\(\s*document\.hidden\s*&&\s*isPlaying\s*\)\s*togglePlayPause\(\);\s*\}\s*\);"
)

AV_VISIBILITY_FIX = """document.addEventListener('visibilitychange', () => {
    // Pause on screen-lock, resume on return. The old code called
    // togglePlayPause() and never came back, so locking the phone
    // ended playback for good.
    if (!synth) return;
    if (document.hidden && isPlaying) { try { synth.pause(); } catch (e) {} }
    else if (!document.hidden && isPlaying) { try { synth.resume(); } catch (e) {} }
});"""

AV_DEVICECHANGE = re.compile(
    r"navigator\.mediaDevices\.addEventListener\(\s*'devicechange'\s*,\s*\(\)\s*=>\s*\{\s*"
    r"if\s*\(\s*isPlaying\s*\)\s*togglePlayPause\(\);\s*\}\s*\);"
)

AV_DEVICECHANGE_FIX = """navigator.mediaDevices.addEventListener('devicechange', () => {
        if (isPlaying && synth) { try { synth.pause(); } catch (e) {} }
    });"""

AV_ONERROR = re.compile(
    r"currentUtterance\.onerror\s*=\s*\(e\)\s*=>\s*\{\s*"
    r"if\s*\(\s*e\.error\s*!==\s*'interrupted'\s*&&\s*e\.error\s*!==\s*'canceled'\s*&&\s*isPlaying\s*\)\s*"
    r"\{\s*stopTTS\(\);\s*\}\s*\};"
)

AV_ONERROR_FIX = """currentUtterance.onerror = (e) => {
        // A transient engine glitch used to kill the whole session. Skip the
        // failed chunk and keep going instead.
        if (e.error === 'interrupted' || e.error === 'canceled' || !isPlaying) return;
        jssAdvanceAfterError();
    };"""

# The magazines feed a whole paragraph to one utterance. Chrome and Android
# truncate or fail on long utterances, which is why magazine audio dropped
# mid-paragraph. Split on the same punctuation set the books use.
AV_CHUNKER = """
/* jss-patch: chunker ported from the book engine */
function chunkText(text) {
    let segments = text.match(/[^।॥.!?,;:\\n]+[।॥.!?,;:\\n]*/g);
    if (!segments) return [text];
    let out = [], cur = "";
    segments.forEach(seg => {
        if ((cur.length + seg.length) > 120) {
            if (cur.trim()) out.push(cur.trim());
            cur = seg;
        } else { cur += seg; }
    });
    if (cur.trim()) out.push(cur.trim());
    let final = [];
    out.forEach(chunk => {
        if (chunk.length > 150) {
            let temp = "";
            chunk.split(' ').forEach(w => {
                if ((temp.length + w.length) > 120) { final.push(temp.trim()); temp = w + " "; }
                else { temp += w + " "; }
            });
            if (temp.trim()) final.push(temp.trim());
        } else { final.push(chunk); }
    });
    return final;
}
"""


def patch_av_engine(html: str) -> tuple[str, list[str]]:
    notes = []
    new, n = AV_VISIBILITY.subn(AV_VISIBILITY_FIX, html, count=1)
    if n:
        notes.append("screen-lock now pauses and resumes (was: stopped for good)")
    html = new

    new, n = AV_DEVICECHANGE.subn(AV_DEVICECHANGE_FIX, html, count=1)
    if n:
        notes.append("headphone change pauses instead of stopping")
    html = new

    new, n = AV_ONERROR.subn(AV_ONERROR_FIX, html, count=1)
    if n:
        notes.append("TTS errors recover instead of ending the session")
    html = new

    if "jss-patch: chunker" not in html:
        m = re.search(r"function cleanTextForSpeech", html)
        if m:
            html = html[: m.start()] + AV_CHUNKER + "\n" + html[m.start():]
            notes.append("added text chunking (fixes mid-paragraph dropouts)")

    return html, notes




AV_PLAYBACK_OVERRIDE = """
/* jss-patch: chunk-aware playback for the magazine engine.
   The original readCurrentElement() handed a whole paragraph to one
   utterance; Chrome and Android truncate long utterances, which is why
   magazine audio stopped mid-paragraph. This replaces it with the same
   chunk-at-a-time loop the books use. Declared last so it wins. */
(function () {
  if (typeof chunkText !== 'function') return;
  var chunks = [], ci = 0, skipNext = false;

  window.readCurrentElement = function (startCharIndex) {
    startCharIndex = startCharIndex || 0;
    if (currentIndex >= ttsElements.length) { stopTTS(); return; }
    ttsElements.forEach(function (el) { el.classList.remove('tts-active'); });

    var el = ttsElements[currentIndex];
    el.classList.add('tts-active');
    try { el.scrollIntoView({ behavior: 'auto', block: 'center' }); } catch (e) {}
    if (typeof updateProgressDisplay === 'function') updateProgressDisplay();
    if (!synth) return;

    var text = cleanTextForSpeech(el.innerText || el.textContent || '');
    skipNext = false;
    if (el.classList.contains('read-next') && (currentIndex + 1) < ttsElements.length) {
      var nx = ttsElements[currentIndex + 1];
      nx.classList.add('tts-active');
      text += ' ' + cleanTextForSpeech(nx.innerText || nx.textContent || '');
      skipNext = true;
    }
    if (!text) { currentIndex += skipNext ? 2 : 1; window.readCurrentElement(0); return; }

    chunks = chunkText(text);
    ci = 0;
    if (startCharIndex > 0) {
      var acc = 0;
      for (var i = 0; i < chunks.length; i++) {
        if (acc + chunks[i].length >= startCharIndex) { ci = i; break; }
        acc += chunks[i].length;
      }
    }
    isPlaying = true;
    speakChunk();
  };

  function speakChunk() {
    if (ci >= chunks.length) {
      currentIndex += skipNext ? 2 : 1;
      skipNext = false;
      currentBoundaryIndex = 0;
      if (isPlaying) setTimeout(function () { window.readCurrentElement(0); }, 60);
      return;
    }
    if (!allVoices || allVoices.length === 0) allVoices = synth.getVoices();
    var v = window.jssPickVoice ? window.jssPickVoice(allVoices) : null;

    currentUtterance = new SpeechSynthesisUtterance(chunks[ci]);
    currentUtterance.lang = __LANG__;
    currentUtterance.rate = ttsRate;
    if (v) currentUtterance.voice = v;

    currentUtterance.onboundary = function (e) {
      if (e.name === 'word') currentBoundaryIndex = e.charIndex;
    };
    currentUtterance.onend = function () {
      currentBoundaryIndex = 0;
      if (!isPlaying) return;
      ci++;
      setTimeout(speakChunk, 40);
    };
    currentUtterance.onerror = function (e) {
      if (e.error === 'interrupted' || e.error === 'canceled' || !isPlaying) return;
      ci++;
      setTimeout(speakChunk, 60);
    };

    if (typeof updatePlayerUI === 'function') updatePlayerUI();
    if (typeof startHeartbeat === 'function' && !(typeof isFirefox !== 'undefined' && isFirefox)) startHeartbeat();
    if (typeof savePlaybackState === 'function') savePlaybackState();
    synth.speak(currentUtterance);
  }
})();
"""

# --------------------------------------------------------------------------
# 4b. route voice selection through the ranked picker
# --------------------------------------------------------------------------

VOICE_PATTERNS = [
    # books (Gujarati and Hindi)
    (re.compile(r"let\s+(gujaratiVoice|hindiVoice)\s*=\s*voices\.find\([^;]*?\);"),
     r"let \1 = (window.jssPickVoice ? window.jssPickVoice(voices) : null);"),
    # Gujarati magazines
    (re.compile(r"let\s+gujVoice\s*=\s*allVoices\.find\([^;]*?\);"),
     "let gujVoice = (window.jssPickVoice ? window.jssPickVoice(allVoices) : null);"),
    # Hindi magazines (two-line, already had a Natural preference)
    (re.compile(r"let\s+hiVoice\s*=\s*allVoices\.find\([^;]*?\)\s*\|\|\s*\n?\s*allVoices\.find\([^;]*?\);"),
     "let hiVoice = (window.jssPickVoice ? window.jssPickVoice(allVoices) : null);"),
]


def patch_voice_selection(html: str) -> tuple[str, int]:
    """Prefer Natural/Neural voices everywhere, as the Hindi magazines did."""
    if "jssPickVoice(" in html:
        return html, 0
    total = 0
    for pat, rep in VOICE_PATTERNS:
        html, n = pat.subn(rep, html)
        total += n
    return html, total


# --------------------------------------------------------------------------
# 4c. never refuse to speak just because no exact voice matched
# --------------------------------------------------------------------------
# The 17 books did:
#     if (!gujaratiVoice) { showBanner(); stopTTS(); return; }
# The 6 magazines instead set utterance.lang and let the browser choose.
# Chrome on desktop ships no Gujarati voice, so on the books the Play button
# silently did nothing. Make the books fall back the same way.

NO_VOICE_REFUSAL = re.compile(
    r"if\s*\(\s*!(?P<v>gujaratiVoice|hindiVoice)\s*\)\s*\{"
    r"(?P<body>(?:[^{}]|\{[^{}]*\})*?)"
    r"stopTTS\(\);\s*return;\s*\}",
    re.S,
)

NO_VOICE_FALLBACK = """if (!\g<v>) {
    // jss-patch: don't refuse. Show the hint, then speak anyway with
    // utterance.lang set so the browser can pick a fallback voice -- this
    // is what the magazines always did, and it is why Chrome on desktop
    // (which ships no Gujarati voice) produced no audio here.
    let nvBanner = document.getElementById("no-voice-banner");
    if (nvBanner) nvBanner.style.display = "block";
  }"""


def patch_no_voice_refusal(html: str) -> tuple[str, int]:
    if "jss-patch: don't refuse" in html:
        return html, 0
    return NO_VOICE_REFUSAL.subn(NO_VOICE_FALLBACK, html)

# --------------------------------------------------------------------------
# 5. player UI
# --------------------------------------------------------------------------

SPEED_BUTTON = re.compile(r"<button\s+id=\"speed-btn\"[^>]*>.*?</button>", re.S)

SPEED_SLIDER = """<div class="tts-speed-wrap">
      <label for="speed-slider" lang="en">Speed</label>
      <input type="range" id="speed-slider" min="0.5" max="2" step="0.1" value="0.9"
             aria-label="Speech speed" oninput="jssSetSpeed(this.value)">
      <span id="speed-readout" lang="en">0.9x</span>
    </div>"""

SPEED_CSS = """
/* jss-patch: speed slider, labelled controls, resume banner */
.tts-speed-wrap { display:flex; flex-direction:column; align-items:center; gap:2px; width:100%; }
.tts-speed-wrap label { font-size:10px; letter-spacing:1px; text-transform:uppercase; color:#aaa; }
.tts-speed-wrap input[type=range] { width:104px; accent-color:#f57c00; cursor:pointer; margin:0; }
.tts-speed-wrap input[type=range]:focus-visible { outline:2px solid #ffb74d; outline-offset:2px; }
#speed-readout { font-size:11px; color:#ddd; font-weight:700; }
#jss-resume-banner {
  position:fixed; left:50%; transform:translateX(-50%);
  bottom:calc(96px + env(safe-area-inset-bottom));
  background:#1565c0; color:#fff; padding:10px 16px; border-radius:10px;
  box-shadow:0 6px 20px rgba(0,0,0,.35); z-index:1200; display:none;
  font-size:14px; align-items:center; gap:12px; font-family:sans-serif;
  max-width:calc(100vw - 32px);
}
#jss-resume-banner button {
  background:#fff; color:#1565c0; border:0; border-radius:6px;
  padding:6px 12px; font-weight:700; cursor:pointer; font-size:13px;
}
#jss-resume-banner button.jss-dismiss { background:transparent; color:#cfe3ff; }
@media print { #jss-resume-banner { display:none !important; } }
"""


def patch_player_ui(html: str) -> tuple[str, list[str]]:
    notes = []

    if 'id="speed-slider"' not in html:
        new, n = SPEED_BUTTON.subn(SPEED_SLIDER, html, count=1)
        if n:
            notes.append("speed slider replaces the cycling button")
        html = new

    before = html
    for a, b, c in (("ક", "ક+", "ક++"), ("क", "क+", "क++")):
        html = html.replace(f"['{a}', '{b}', '{c}']", "['Zoom', 'Zoom+', 'Zoom++']")
        html = html.replace(f"['{a}','{b}','{c}']", "['Zoom', 'Zoom+', 'Zoom++']")
    html = html.replace("'🔤 ' + labels[", "'🔍 ' + labels[")
    html = html.replace("'🔤 ' + ['Zoom', 'Zoom+', 'Zoom++'][", "'🔍 ' + ['Zoom', 'Zoom+', 'Zoom++'][")
    html = re.sub(
        r'(<button[^>]*id="font-size-btn"[^>]*>)\s*🔤\s*[કक]\s*(</button>)',
        r'\1🔍 <span lang="en">Zoom</span>\2',
        html,
    )
    if html != before:
        notes.append("zoom control relabelled to English")

    before = html
    html = re.sub(
        r'(<button[^>]*id="theme-toggle-btn"[^>]*>)\s*🌓\s*(</button>)',
        r'\1🌓 <span lang="en" id="theme-label">Dark</span>\2',
        html,
    )
    html = re.sub(
        r'<button\s+class="btn-theme"\s+onclick="manualThemeToggle\(\)"[^>]*>\s*🌓\s*</button>',
        '<button class="btn-theme" id="theme-toggle-btn" onclick="manualThemeToggle()" '
        'title="Toggle Dark Mode">🌓 <span lang="en" id="theme-label">Dark</span></button>',
        html,
    )
    if html != before:
        notes.append("dark-mode button labelled")

    before = html
    html = html.replace('<div class="quick-nav no-print" aria-hidden="true">',
                        '<div class="quick-nav no-print" role="group" aria-label="Reading controls">')
    html = html.replace('<div id="tts-player" class="no-print" aria-hidden="true">',
                        '<div id="tts-player" class="no-print" role="group" aria-label="Audio player">')
    if html != before:
        notes.append("controls exposed to assistive tech")

    if "jss-patch: speed slider" not in html:
        idx = html.rfind("</style>")
        if idx != -1:
            html = html[:idx] + SPEED_CSS + html[idx:]

    return html, notes


# --------------------------------------------------------------------------
# 6. runtime block
# --------------------------------------------------------------------------

RUNTIME = """
@@MARK@@
// =========================================================================
//  jss-patch runtime: voice quality, mediaSession, speed slider,
//  resume banner, error recovery, cross-browser normalisation.
// =========================================================================
(function () {
  var LANG = (typeof JSS_LANG === 'string' && JSS_LANG) ? JSS_LANG : 'gu';

  // ---- neural voice preference ----------------------------------------
  // The Hindi magazines already did this; nothing else did, so readers with
  // a Natural/Neural voice installed were getting the older robotic one.
  // Strictly scoped to this page's language. Never cross languages: a
  // Hindi voice reading Gujarati script produces wrong pronunciation, so
  // no voice at all is better than the wrong one.
  var LANG_NAME = { gu: 'gujarati', hi: 'hindi' }[LANG] || '';

  window.jssPickVoice = function (voices) {
    if (!voices || !voices.length) return null;
    var pool = voices.filter(function (v) {
      return v.lang && v.lang.toLowerCase().replace('_', '-').indexOf(LANG + '-') === 0;
    });
    if (!pool.length) {
      pool = voices.filter(function (v) {
        return v.lang && v.lang.toLowerCase() === LANG;
      });
    }
    if (!pool.length && LANG_NAME) {
      pool = voices.filter(function (v) {
        return (v.name || '').toLowerCase().indexOf(LANG_NAME) !== -1;
      });
    }
    if (!pool.length) return null;
    var ranked = ['natural', 'neural', 'online', 'enhanced', 'premium'];
    for (var i = 0; i < ranked.length; i++) {
      var want = ranked[i];
      var hit = pool.filter(function (v) {
        return (v.name || '').toLowerCase().indexOf(want) !== -1;
      })[0];
      if (hit) return hit;
    }
    return pool[0];
  };

  // ---- speed slider ----------------------------------------------------
  window.jssSetSpeed = function (value) {
    var v = Math.max(0.5, Math.min(2, parseFloat(value) || 0.9));
    try { ttsRate = v; } catch (e) {}
    if (typeof rateSteps !== 'undefined' && typeof rateIndex !== 'undefined') {
      var best = 0, bestD = Infinity;
      for (var i = 0; i < rateSteps.length; i++) {
        var d = Math.abs(rateSteps[i] - v);
        if (d < bestD) { bestD = d; best = i; }
      }
      try { rateIndex = best; } catch (e) {}
    }
    var out = document.getElementById('speed-readout');
    if (out) out.textContent = v.toFixed(1) + 'x';
    var sl = document.getElementById('speed-slider');
    if (sl && parseFloat(sl.value) !== v) sl.value = v;
    try { localStorage.setItem(JSSG('rate'), String(v)); } catch (e) {}

    if (typeof isPlaying !== 'undefined' && isPlaying && typeof synth !== 'undefined' && synth) {
      if (typeof speedChangeInFlight !== 'undefined') { speedChangeInFlight = true; }
      try { synth.cancel(); } catch (e) {}
      setTimeout(function () {
        if (typeof speedChangeInFlight !== 'undefined') { speedChangeInFlight = false; }
        if (typeof playChunk === 'function') playChunk(currentBoundaryIndex || 0);
        else if (typeof readCurrentElement === 'function') readCurrentElement(currentBoundaryIndex || 0);
      }, 60);
    }
  };

  // ---- error recovery for the magazine engine --------------------------
  window.jssAdvanceAfterError = function () {
    if (typeof currentIndex === 'undefined') return;
    try { currentIndex += 1; currentBoundaryIndex = 0; } catch (e) {}
    if (typeof readCurrentElement === 'function') {
      setTimeout(function () { readCurrentElement(0); }, 120);
    }
  };

  // ---- resume banner ---------------------------------------------------
  // Every file called getElementById('resume-banner') and got null, so the
  // saved position was never offered. Build it instead of shipping it.
  function buildResumeBanner() {
    if (document.getElementById('jss-resume-banner')) return;
    var saved = jssGet(JSSK('audioIndex'));
    if (saved === null) return;
    var n = parseInt(saved, 10);
    if (isNaN(n) || n <= 0) return;
    if (typeof ttsElements !== 'undefined' && ttsElements.length && n >= ttsElements.length) return;

    var bar = document.createElement('div');
    bar.id = 'jss-resume-banner';
    bar.className = 'no-print';
    bar.setAttribute('role', 'status');
    var pct = (typeof ttsElements !== 'undefined' && ttsElements.length)
      ? ' (' + Math.round((n / ttsElements.length) * 100) + '%)' : '';
    bar.innerHTML =
      '<span>Resume where you left off' + pct + '</span>' +
      '<button type="button" class="jss-go">Resume</button>' +
      '<button type="button" class="jss-dismiss">Start over</button>';
    document.body.appendChild(bar);
    bar.style.display = 'flex';

    bar.querySelector('.jss-go').addEventListener('click', function () {
      bar.remove();
      try { currentIndex = n; } catch (e) {}
      if (typeof readCurrentElement === 'function') readCurrentElement(0);
    });
    bar.querySelector('.jss-dismiss').addEventListener('click', function () {
      bar.remove();
      try {
        localStorage.removeItem(JSSK('audioIndex'));
        localStorage.removeItem(JSSK('audioChunk'));
        localStorage.removeItem(JSSK('audioBoundary'));
      } catch (e) {}
      try { currentIndex = 0; } catch (e) {}
    });
  }

  // ---- magazine-side playback persistence ------------------------------
  if (typeof savePlaybackState !== 'function') {
    window.savePlaybackState = function () {
      try {
        localStorage.setItem(JSSK('audioIndex'), String(currentIndex));
        localStorage.setItem(JSSK('audioBoundary'), String(currentBoundaryIndex || 0));
      } catch (e) {}
    };
    setInterval(function () {
      if (typeof isPlaying !== 'undefined' && isPlaying) window.savePlaybackState();
    }, 4000);
  }

  // ---- mediaSession ----------------------------------------------------
  // Lock-screen, notification-shade, headset and car controls. Also stops
  // Android reclaiming the tab the moment the screen goes off.
  function wireMediaSession() {
    if (!('mediaSession' in navigator)) return;
    try {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: (typeof JSS_BOOK_TITLE === 'string' ? JSS_BOOK_TITLE : document.title),
        artist: 'Dada Bhagwan',
        album: 'JSS Audio Library',
        artwork: [
          { src: '@@IMG192@@', sizes: '192x192', type: 'image/png' },
          { src: '@@IMG512@@', sizes: '512x512', type: 'image/png' }
        ]
      });
      navigator.mediaSession.setActionHandler('play', function () {
        if (typeof isPlaying !== 'undefined' && !isPlaying && typeof togglePlayPause === 'function') togglePlayPause();
      });
      navigator.mediaSession.setActionHandler('pause', function () {
        if (typeof isPlaying !== 'undefined' && isPlaying && typeof togglePlayPause === 'function') togglePlayPause();
      });
      navigator.mediaSession.setActionHandler('stop', function () {
        if (typeof stopTTS === 'function') stopTTS();
      });
      navigator.mediaSession.setActionHandler('previoustrack', function () {
        if (typeof currentIndex === 'undefined') return;
        currentIndex = Math.max(0, currentIndex - 1);
        if (typeof readCurrentElement === 'function') readCurrentElement(0);
      });
      navigator.mediaSession.setActionHandler('nexttrack', function () {
        if (typeof currentIndex === 'undefined' || typeof ttsElements === 'undefined') return;
        currentIndex = Math.min(ttsElements.length - 1, currentIndex + 1);
        if (typeof readCurrentElement === 'function') readCurrentElement(0);
      });
    } catch (e) {}
  }

  window.jssSyncMediaState = function () {
    if (!('mediaSession' in navigator)) return;
    try {
      navigator.mediaSession.playbackState =
        (typeof isPlaying !== 'undefined' && isPlaying) ? 'playing' : 'paused';
    } catch (e) {}
  };

  // ---- dark-mode label -------------------------------------------------
  window.jssSyncThemeLabel = function () {
    var el = document.getElementById('theme-label');
    if (!el) return;
    var dark = document.body.classList.contains('dark-saver');
    el.textContent = dark ? 'Light' : 'Dark';
    var btn = document.getElementById('theme-toggle-btn');
    if (btn) {
      btn.setAttribute('aria-pressed', dark ? 'true' : 'false');
      btn.setAttribute('title', dark ? 'Switch to light mode' : 'Switch to dark mode');
    }
  };

  // ---- Firefox: pause()/resume() is unreliable, drop the heartbeat -----
  if (typeof isFirefox !== 'undefined' && isFirefox) {
    var kill = setInterval(function () {
      if (typeof heartbeatTimer !== 'undefined' && heartbeatTimer) clearInterval(heartbeatTimer);
      if (typeof heartbeatInterval !== 'undefined' && heartbeatInterval) clearInterval(heartbeatInterval);
    }, 2000);
    window.addEventListener('pagehide', function () { clearInterval(kill); });
  }


  // ---- play/pause intent guard ----------------------------------------
  // Pausing used to need two or three taps: onend and the iOS failsafe had
  // already queued setTimeout(playChunk, ...) calls, and playChunk sets
  // isPlaying = true unconditionally, so playback resurrected itself.
  // Record what the user actually asked for and refuse stale restarts.
  window.__jssWant = 'stop';
  var jssGen = 0;

  // Time-boxed on purpose. The resurrection race happens within a few
  // hundred milliseconds of a pause, so a short window is enough -- and it
  // means a stuck 'stop' can never permanently block the Play button.
  window.__jssStopAt = 0;
  var STOP_WINDOW_MS = 1500;

  function guard(name) {
    if (typeof window[name] !== 'function') return;
    var orig = window[name];
    window[name] = function () {
      if (window.__jssWant === 'stop' &&
          (Date.now() - window.__jssStopAt) < STOP_WINDOW_MS) return;
      return orig.apply(this, arguments);
    };
  }

  function watchdog() {
    // If the engine keeps talking after a pause, cut it off.
    var g = ++jssGen;
    var t = setInterval(function () {
      if (jssGen !== g || window.__jssWant !== 'stop') { clearInterval(t); return; }
      if (typeof synth !== 'undefined' && synth && (synth.speaking || synth.pending)) {
        try { synth.cancel(); } catch (e) {}
      }
    }, 120);
    setTimeout(function () { clearInterval(t); }, 4000);
  }

  // ---- URL <-> page number --------------------------------------------
  // Books use id="page-N", magazines id="pageN". Keep the address bar in
  // step with what is on screen so any page can be linked or shared.
  function wirePageHash() {
    var pages = Array.prototype.slice.call(
      document.querySelectorAll('.page-container, .av-page-wrapper')
    ).filter(function (p) { return /^page-?\d+$/.test(p.id || ''); });
    if (!pages.length) return;

    var current = null;
    function setHash(id) {
      if (!id || id === current) return;
      current = id;
      try { history.replaceState(null, '', location.pathname + location.search + '#' + id); }
      catch (e) {}
    }

    if ('IntersectionObserver' in window) {
      var visible = new Map();
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) visible.set(en.target.id, en.intersectionRatio);
          else visible.delete(en.target.id);
        });
        var best = null, bestR = 0;
        visible.forEach(function (r, id) { if (r > bestR) { bestR = r; best = id; } });
        if (best) setHash(best);
      }, { threshold: [0.1, 0.5, 0.9], rootMargin: '-10% 0px -60% 0px' });
      pages.forEach(function (p) { io.observe(p); });
    } else {
      var tick = null;
      window.addEventListener('scroll', function () {
        if (tick) return;
        tick = setTimeout(function () {
          tick = null;
          var best = null, bestTop = -Infinity;
          pages.forEach(function (p) {
            var t = p.getBoundingClientRect().top;
            if (t <= 80 && t > bestTop) { bestTop = t; best = p.id; }
          });
          if (best) setHash(best);
        }, 200);
      }, { passive: true });
    }

    // honour an incoming #page-N (browsers often miss it on a long page)
    var want = (location.hash || '').replace('#', '');
    if (want && /^page-?\d+$/.test(want)) {
      var target = document.getElementById(want);
      if (target) setTimeout(function () {
        target.scrollIntoView({ behavior: 'auto', block: 'start' });
      }, 350);
    }
  }


  // ---- actionable no-voice guidance ------------------------------------
  // The stock banner told Chrome users to "open in Chrome". Explain what is
  // actually wrong and how to fix it on the browser they are really using.
  function explainNoVoice() {
    var el = document.getElementById('no-voice-banner');
    if (!el) return;
    var gu = (LANG === 'gu');
    var isAndroid = /Android/i.test(navigator.userAgent);
    var isiOS = /iPhone|iPad|iPod/i.test(navigator.userAgent);
    var chrome = /Chrome/i.test(navigator.userAgent) && !(typeof isEdgeUA !== 'undefined' && isEdgeUA);
    var langName = gu ? 'ગુજરાતી' : 'हिन्दी';
    var head = gu
      ? '\u26a0\ufe0f <b>નોંધ:</b> આ બ્રાઉઝરમાં ' + langName + ' અવાજ ઇન્સ્ટોલ થયેલો નથી.'
      : '\u26a0\ufe0f <b>ध्यान दें:</b> इस ब्राउज़र में ' + langName + ' आवाज़ इंस्टॉल नहीं है।';
    var how;
    if (isiOS) {
      how = gu
        ? 'iPhone / iPad ' + langName + ' અવાજને સપોર્ટ કરતું નથી. કૃપા કરીને <b>Microsoft Edge</b> માં ખોલો \u2192 <b>Read Aloud</b>.'
        : 'iPhone / iPad ' + langName + ' आवाज़ को सपोर्ट नहीं करता। कृपया <b>Microsoft Edge</b> में खोलें \u2192 <b>Read Aloud</b>.';
    } else if (isAndroid) {
      how = gu
        ? '<b>Settings \u2192 Accessibility \u2192 Text-to-speech \u2192 Install voice data \u2192 ' + langName + '</b> ઇન્સ્ટોલ કરો, પછી આ પાનું ફરી ખોલો.'
        : '<b>Settings \u2192 Accessibility \u2192 Text-to-speech \u2192 Install voice data \u2192 ' + langName + '</b> इंस्टॉल करें, फिर यह पेज दोबारा खोलें।';
    } else if (chrome) {
      how = gu
        ? 'Windows માં <b>Settings \u2192 Time &amp; Language \u2192 Speech \u2192 Add voices \u2192 ' + langName + '</b> ઉમેરો, અથવા આ પાનું <b>Microsoft Edge</b> માં ખોલો (ત્યાં ' + langName + ' અવાજ તૈયાર મળે છે).'
        : 'Windows में <b>Settings \u2192 Time &amp; Language \u2192 Speech \u2192 Add voices \u2192 ' + langName + '</b> जोड़ें, या यह पेज <b>Microsoft Edge</b> में खोलें।';
    } else {
      how = gu
        ? 'સિસ્ટમમાં ' + langName + ' અવાજ ઉમેરો, અથવા આ પાનું <b>Microsoft Edge</b> માં ખોલો.'
        : 'सिस्टम में ' + langName + ' आवाज़ जोड़ें, या यह पेज <b>Microsoft Edge</b> में खोलें।';
    }
    el.innerHTML = head + ' ' + how;
  }

  // ---- wrap the originals ---------------------------------------------
  document.addEventListener('DOMContentLoaded', function () {
    wireMediaSession();
    jssSyncThemeLabel();

    if (typeof manualThemeToggle === 'function') {
      var origTheme = manualThemeToggle;
      manualThemeToggle = function () { origTheme.apply(this, arguments); jssSyncThemeLabel(); };
    }
    if (typeof updatePlayerUI === 'function') {
      var origUI = updatePlayerUI;
      updatePlayerUI = function () { origUI.apply(this, arguments); jssSyncMediaState(); };
    }

    // intent must be recorded before the original runs, because the original
    // is what flips isPlaying.
    if (typeof togglePlayPause === 'function') {
      var origToggle = togglePlayPause;
      togglePlayPause = function () {
        var goingToPlay = !(typeof isPlaying !== 'undefined' && isPlaying);
        window.__jssWant = goingToPlay ? 'play' : 'stop';
        if (!goingToPlay) window.__jssStopAt = Date.now();
        var r = origToggle.apply(this, arguments);
        if (!goingToPlay) watchdog();
        jssSyncMediaState();
        return r;
      };
    }
    if (typeof stopTTS === 'function') {
      var origStop = stopTTS;
      stopTTS = function () {
        window.__jssWant = 'stop';
        window.__jssStopAt = Date.now();
        var r = origStop.apply(this, arguments);
        watchdog();
        jssSyncMediaState();
        return r;
      };
    }
    if (typeof playFromIndex === 'function') {
      var origPlayFrom = playFromIndex;
      playFromIndex = function () {
        // The original calls stopTTS() first, which would set intent to
        // 'stop' and block the deferred start. Assert 'play' afterwards.
        var r = origPlayFrom.apply(this, arguments);
        window.__jssWant = 'play';
        return r;
      };
    }
    ['playChunk', 'readCurrentElement'].forEach(guard);

    wirePageHash();
    explainNoVoice();

    var saved = parseFloat(jssGet(JSSG('rate')));
    if (!isNaN(saved) && saved >= 0.5 && saved <= 2) jssSetSpeed(saved);
    else if (typeof ttsRate !== 'undefined') jssSetSpeed(ttsRate);

    setTimeout(buildResumeBanner, 600);
  });
})();
"""


def runtime_block() -> str:
    return (RUNTIME
            .replace("@@MARK@@", JS_MARK)
            .replace("@@IMG192@@", BASE_URL + "icon-192.png")
            .replace("@@IMG512@@", BASE_URL + "icon-512.png"))


# --------------------------------------------------------------------------
# 7. metadata
# --------------------------------------------------------------------------

def patch_metadata(html: str, url: str, title: str, lang: str) -> tuple[str, list[str]]:
    notes = []

    new = re.sub(r"<!--\s*\^\^\s*REPLACE.*?-->\s*", "", html, flags=re.S)
    if new != html:
        notes.append("removed build-instruction comment left in production")
    html = new

    if "Everywherea" in html:
        html = html.replace("Everywherea", "Everywhere")
        notes.append("fixed 'Everywherea' typo")

    new, n = re.subn(
        r'(<meta\s+property="og:url"\s+content=")[^"]*(")',
        lambda m: m.group(1) + url + m.group(2),
        html,
    )
    if n and new != html:
        notes.append("og:url now points at this page, not the site root")
    html = new

    head_close = html.find("</head>")
    if head_close == -1:
        return html, notes

    add = []
    if 'rel="canonical"' not in html:
        add.append(f'<link rel="canonical" href="{url}"/>')
        notes.append("canonical added")
    if "og:image" not in html:
        add.append(f'<meta property="og:image" content="{BASE_URL}{SHARE_IMAGE}"/>')
        add.append('<meta property="og:image:width" content="512"/>')
        add.append('<meta property="og:image:height" content="512"/>')
        add.append('<meta name="twitter:card" content="summary"/>')
        notes.append("og:image added (share previews had no thumbnail)")
    if "og:title" not in html:
        locale = "hi_IN" if lang == "hi" else "gu_IN"
        add.append('<meta property="og:site_name" content="Dada Bhagwan — Jai Sachchidanand Sangh"/>')
        add.append(f'<meta property="og:title" content="{title}"/>')
        add.append('<meta property="og:type" content="article"/>')
        add.append(f'<meta property="og:locale" content="{locale}"/>')
        add.append(f'<meta property="og:url" content="{url}"/>')
        notes.append("full OG block added (page had none)")
    if 'rel="manifest"' not in html:
        depth = url.replace(BASE_URL, "").count("/")
        prefix = "../" * depth
        add.append(f'<link rel="manifest" href="{prefix}manifest.json"/>')
        add.append(f'<link rel="apple-touch-icon" href="{prefix}icon-192.png"/>')
        notes.append("manifest + touch icon linked")

    if add:
        block = f"\n{MARK_OPEN}\n" + "\n".join(add) + f"\n<!-- {MARK}:end -->\n"
        html = html[:head_close] + block + html[head_close:]

    return html, notes


# --------------------------------------------------------------------------
# 8. structural fix
# --------------------------------------------------------------------------

def patch_structure(html: str) -> tuple[str, list[str]]:
    notes = []
    # Three books open <head> and never close it, so nothing could be
    # appended to the head and browsers were guessing where <body> began.
    if "<head>" in html and "</head>" not in html:
        m = re.search(r"<body[^>]*>", html)
        if m:
            html = html[: m.start()] + "</head>\n" + html[m.start():]
            notes.append("added the missing </head> tag")
    if re.search(r"</body>\s*<script>", html):
        html = re.sub(r"</body>\s*(<script>)", r"\1", html, count=1)
        html = re.sub(r"(</script>)\s*</html>\s*$", r"\1\n</body>\n</html>\n", html, count=1)
        notes.append("moved trailing <script> inside <body> (was after </body>)")
    return html, notes


# --------------------------------------------------------------------------
# per-file driver
# --------------------------------------------------------------------------

def patch_html(root: Path, path: Path, orphan: bool = False) -> tuple[str, list[str]]:
    html = path.read_text(encoding="utf-8")
    notes: list[str] = []

    lang = "hi" if re.search(r'<html[^>]*lang="hi"', html) else "gu"
    lang_code = lang
    av_engine = is_av_engine(html)
    title = page_title(html)
    bid = book_id(root, path)
    url = rel_url(root, path)

    html, n = patch_cross_browser(html)
    if n:
        notes.append("Chrome / Edge / Firefox now share one player")

    if av_engine:
        html, av_notes = patch_av_engine(html)
        notes += av_notes

    html, keys = patch_storage_keys(html)
    if keys:
        notes.append(f"namespaced {keys} storage call(s) to this book")

    html, nv = patch_voice_selection(html)
    if nv:
        notes.append(f"{nv} voice lookup(s) now prefer Natural/Neural voices")



    if JS_MARK not in html:
        anchor = "const isEdge = false;"
        pos = html.find(anchor)
        if pos != -1:
            pos += len(anchor)
            html = html[:pos] + "\n" + js_preamble(bid, title, lang) + html[pos:]
        else:
            m = re.search(r"<script>", html)
            if m:
                html = html[: m.end()] + "\n" + js_preamble(bid, title, lang) + html[m.end():]

    html, struct_notes = patch_structure(html)
    notes += struct_notes

    html, ui_notes = patch_player_ui(html)
    notes += ui_notes

    html, meta_notes = patch_metadata(html, url, title, lang)
    notes += meta_notes

    if "jss-patch runtime" not in html:
        idx = html.rfind("</script>")
        if idx != -1:
            html = html[:idx] + runtime_block() + "\n" + html[idx:]
            notes.append("runtime block appended (mediaSession, voices, resume)")

    if av_engine and "jss-patch: chunk-aware playback" not in html:
        idx = html.rfind("</script>")
        if idx != -1:
            lang = '"hi-IN"' if lang_code == "hi" else '"gu-IN"'
            html = html[:idx] + AV_PLAYBACK_OVERRIDE.replace("__LANG__", lang) + "\n" + html[idx:]
            notes.append("magazine playback now chunked (was one utterance per paragraph)")

    if orphan and "noindex" not in html:
        head = html.find("</head>")
        if head != -1:
            html = html[:head] + NOINDEX + html[head:]
            notes.append("marked noindex (superseded edition was competing in search)")

    return html, notes


# --------------------------------------------------------------------------
# index.html / manifest.json / sw.js / orphans
# --------------------------------------------------------------------------

INDEX_META = """
<!-- {mark}:start -->
<meta name="description" content="Free Gujarati and Hindi audio e-books of Dada Bhagwan's Akram Vignan satsangs — {books} books, {pages} pages, with read-along text-to-speech. Published by Jai Sachchidanand Sangh."/>
<meta name="robots" content="index, follow"/>
<link rel="canonical" href="{base}"/>
<meta property="og:site_name" content="Dada Bhagwan — Jai Sachchidanand Sangh"/>
<meta property="og:title" content="JSS Audio Library — Akram Vignan Audio e-Books"/>
<meta property="og:description" content="{books} books, {pages} pages in Gujarati and Hindi. Listen and read along, free."/>
<meta property="og:type" content="website"/>
<meta property="og:url" content="{base}"/>
<meta property="og:image" content="{base}icon-512.png"/>
<meta property="og:image:width" content="512"/>
<meta property="og:image:height" content="512"/>
<meta name="twitter:card" content="summary"/>
<!-- {mark}:end -->
"""


def library_counts(root: Path) -> tuple[int, int, list[str]]:
    idx = (root / "index.html").read_text(encoding="utf-8")
    rows = re.findall(r'href="([^"]+\.html)"\s+data-pages="(\d+)"', idx)
    fixes, total = [], 0
    for href, declared in rows:
        f = root / href
        if not f.is_file():
            continue
        nums = [int(x) for x in re.findall(
            r'id="page-?(\d+)"', f.read_text(encoding="utf-8", errors="ignore"))]
        actual = max(nums) if nums else int(declared)
        total += actual
        if actual != int(declared):
            fixes.append(f"{href.split('/')[-1]}: {declared} -> {actual}")
    return len(rows), total, fixes


def patch_index(root: Path) -> tuple[str, list[str]]:
    path = root / "index.html"
    html = path.read_text(encoding="utf-8")
    notes = []
    books, pages, fixes = library_counts(root)

    for href, declared in re.findall(r'href="([^"]+\.html)"\s+data-pages="(\d+)"', html):
        f = root / href
        if not f.is_file():
            continue
        nums = [int(x) for x in re.findall(
            r'id="page-?(\d+)"', f.read_text(encoding="utf-8", errors="ignore"))]
        actual = max(nums) if nums else int(declared)
        if actual != int(declared):
            html = html.replace(f'href="{href}" data-pages="{declared}"',
                                f'href="{href}" data-pages="{actual}"')
    if fixes:
        notes.append("corrected data-pages: " + "; ".join(fixes))

    before = html
    html = re.sub(r'(<span id="totalBooks">)\d+(</span>)', rf'\g<1>{books}\g<2>', html)
    html = re.sub(r'(<span id="totalPages">)\d+(</span>)', rf'\g<1>{pages}\g<2>', html)
    if html != before:
        notes.append(f"baked totals into the HTML source: {books} books / {pages} pages "
                     "(crawlers were reading 0)")

    if f"{MARK}:start" not in html:
        head = html.find("</head>")
        if head != -1:
            html = html[:head] + INDEX_META.format(
                mark=MARK, base=BASE_URL, books=books, pages=pages) + html[head:]
            notes.append("added description, OG and canonical to the index")

    return html, notes


MANIFEST = """{
  "id": "/JSS-Audio-Project/",
  "name": "JSS Audio Library",
  "short_name": "JSS Audio",
  "description": "Gujarati and Hindi audio e-books of Dada Bhagwan's Akram Vignan satsangs, with read-along text-to-speech.",
  "lang": "gu",
  "dir": "ltr",
  "start_url": "/JSS-Audio-Project/index.html",
  "scope": "/JSS-Audio-Project/",
  "display": "standalone",
  "orientation": "portrait-primary",
  "background_color": "#ffffff",
  "theme_color": "#2980b9",
  "categories": ["books", "education", "lifestyle"],
  "icons": [
    { "src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any" },
    { "src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any" },
    { "src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable" },
    { "src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable" }
  ]
}
"""

SW = """// JSS Audio Library Service Worker  (jss-patch-v1)
// Stale-while-revalidate for pages, cache-first for fonts, with a size cap
// so a 12 MB library doesn't grow without bound.

const CACHE_NAME = "jss-audio-cache-v3";
const FONT_CACHE = "jss-fonts-v1";
const MAX_ENTRIES = 60;

const SHELL_FILES = [
  "./",
  "./index.html",
  "./manifest.json",
  "./icon-192.png",
  "./icon-512.png"
];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache =>
      // addAll is all-or-nothing; a single 404 used to abort the whole install.
      Promise.allSettled(SHELL_FILES.map(f => cache.add(f)))
    )
  );
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(k => k !== CACHE_NAME && k !== FONT_CACHE).map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

async function trim(cacheName, max) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  if (keys.length <= max) return;
  for (let i = 0; i < keys.length - max; i++) await cache.delete(keys[i]);
}

self.addEventListener("fetch", event => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // Google Fonts: cache-first so Gujarati and Hindi still render offline.
  // The old worker skipped them because they carry no file extension.
  if (url.hostname === "fonts.googleapis.com" || url.hostname === "fonts.gstatic.com") {
    event.respondWith(
      caches.open(FONT_CACHE).then(cache =>
        cache.match(req).then(hit =>
          hit || fetch(req).then(res => {
            if (res && (res.ok || res.type === "opaque")) cache.put(req, res.clone());
            return res;
          }).catch(() => hit)
        )
      )
    );
    return;
  }

  if (url.origin !== self.location.origin) return;

  const p = url.pathname;
  const isTarget =
    p.endsWith(".html") || p.endsWith("/") ||
    p.endsWith(".mp3") || p.endsWith(".vtt") ||
    p.endsWith(".png") || p.endsWith(".css") ||
    p.endsWith(".js")  || p.endsWith(".json");
  if (!isTarget) return;

  event.respondWith(
    caches.open(CACHE_NAME).then(cache =>
      cache.match(req).then(cached => {
        const network = fetch(req).then(res => {
          if (res && res.status === 200) {
            cache.put(req, res.clone()).then(() => trim(CACHE_NAME, MAX_ENTRIES));
          }
          return res;
        }).catch(() => cached);
        return cached || network;
      })
    )
  );
});

self.addEventListener("message", e => {
  if (e.data === "skip-waiting") self.skipWaiting();
});
"""

NOINDEX = f"""{MARK_OPEN}
<meta name="robots" content="noindex, nofollow"/>
<!-- Superseded edition. Kept on disk, not linked from the library index. -->
<!-- {MARK}:end -->
"""


def patch_orphans(root: Path) -> list[tuple[Path, str, list[str]]]:
    """Old editions stay on disk but stop competing with current ones in search."""
    idx = (root / "index.html").read_text(encoding="utf-8")
    linked = {h.split("/")[-1] for h in re.findall(r'href="([^"]+\.html)"', idx)}
    out = []
    for p in sorted(root.rglob("*.html")):
        if p.name == "index.html" or p.name in linked or "tools" in p.parts:
            continue
        if not any(d in p.parts for d in BOOK_DIRS):
            continue
        html = p.read_text(encoding="utf-8")
        if "noindex" in html:
            continue
        head = html.find("</head>")
        if head == -1:
            continue
        out.append((p, html[:head] + NOINDEX + html[head:],
                    ["marked noindex (superseded edition still live in search)"]))
    return out


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def show_diff(path: Path, old: str, new: str, root: Path, limit: int = 120) -> None:
    diff = list(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=str(path.relative_to(root)),
        tofile=str(path.relative_to(root)) + " (patched)", n=2))
    sys.stdout.writelines(diff[:limit])
    if len(diff) > limit:
        print(f"      ... {len(diff) - limit} more diff lines\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--diff", action="store_true", help="print diffs during --dry-run")
    args = ap.parse_args()

    root: Path = args.root.resolve()
    if not (root / "index.html").is_file():
        print(f"index.html not found under {root}", file=sys.stderr)
        return 1

    books, pages, fixes = library_counts(root)
    print(f"Library: {books} books, {pages} pages")
    if fixes:
        print("  data-pages drift: " + "; ".join(fixes))
    if args.report:
        return 0

    changes: list[tuple[Path, str, list[str]]] = []

    idx_html = (root / "index.html").read_text(encoding="utf-8")
    linked = {h.split("/")[-1] for h in re.findall(r'href="([^"]+\.html)"', idx_html)}

    for path in sorted(root.rglob("*.html")):
        if not any(d in path.parts for d in BOOK_DIRS):
            continue
        new, notes = patch_html(root, path, orphan=path.name not in linked)
        if new != path.read_text(encoding="utf-8"):
            changes.append((path, new, notes))

    new_index, index_notes = patch_index(root)
    if new_index != (root / "index.html").read_text(encoding="utf-8"):
        changes.append((root / "index.html", new_index, index_notes))

    if (root / "manifest.json").read_text(encoding="utf-8") != MANIFEST:
        changes.append((root / "manifest.json", MANIFEST,
                        ["rewritten: maskable icons, scope, id; dropped invalid splash_screens"]))
    if (root / "sw.js").read_text(encoding="utf-8") != SW:
        changes.append((root / "sw.js", SW,
                        ["rewritten: offline fonts, 60-entry cap, resilient install"]))


    print(f"\n{len(changes)} file(s) to change\n")
    for path, new, notes in changes:
        print(f"  {path.relative_to(root)}")
        for n in notes:
            print(f"      - {n}")
        if args.dry_run and args.diff:
            show_diff(path, path.read_text(encoding="utf-8"), new, root)

    if args.dry_run:
        print("\n(dry run — nothing written)")
        return 0

    for path, new, _ in changes:
        path.write_text(new, encoding="utf-8")
    print(f"\nWrote {len(changes)} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
