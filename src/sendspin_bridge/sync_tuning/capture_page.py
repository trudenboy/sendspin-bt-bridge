"""The phone capture page: a small standalone page the phone opens from the QR code.

No SPA, no sign-in: the page knows only its capture token, which allows reading the session's
capture state and uploading a recording (``/api/v1/sync/capture/{token}``). It follows the session
live (polling once a second): which speaker plays alone now, which is next. In a secure context
(HTTPS or localhost) it records with the browser's microphone (MediaRecorder) and uploads by itself
when the sequence ends; on plain HTTP (a phone opening http://<LAN IP>:8080 cannot use the microphone
in the browser) it asks for a recording made with the phone's own recorder (``<input type=file
capture>``) and uploads that file. Inline script and style, guarded by a per-response CSP nonce.
"""

from __future__ import annotations

import html
import json

TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "title": "Measure with this phone",
        "group": "Group",
        "how": "The bridge plays the clicks on each speaker alone, one after the other, while this phone records once. Keep the room quiet.",
        "step1_live": "Tap Start recording. Keep this page open.",
        "step1_file": "Start your phone's recorder with the button below (or its voice recorder app) and keep it recording.",
        "step2": "On the bridge's page, press “Play the sequence”.",
        "step3_live": "Hold the phone as this page says. The recording stops and is sent by itself at the end.",
        "step3_file": "When the sequence has finished, stop recording and send the recording (if your recorder saved a file, pick it below).",
        "device": "Hold the phone 30–50 cm from the speaker that plays; move to the next one in the pause.",
        "position": "Stay where you usually listen and hold the phone still.",
        "start": "Start recording",
        "stop": "Stop and send",
        "recording": "Recording…",
        "record_file": "Record with the phone",
        "pick": "Or choose a recording",
        "sending": "Sending the recording…",
        "sent": "Recording measured.",
        "waiting": "Waiting for the bridge: press “Play the sequence” there.",
        "lead": "Starting: everything is quiet for a moment…",
        "now": "Now: {name} alone ({n}/{total})",
        "next": "Next: {name} — move to it",
        "pause": "Short pause…",
        "done": "The sequence has finished.",
        "done_file": "The sequence has finished: stop recording and send it.",
        "failed": "The sequence stopped ({code}). Start it again on the bridge's page.",
        "no_clicks": "The clicks are not playing: start them on the bridge's page.",
        "not_measuring": "The measurement is over; you can close this page.",
        "gone": "This link does not work any more. Open a new one from the bridge's page.",
        "http_note": "This page is not on HTTPS, so the browser cannot use the microphone here: record with the phone's recorder instead.",
        "results": "Results",
        "ok": "heard",
        "not_heard": "not heard",
        "unstable": "unstable",
        "skipped": "skipped",
        "error": "That did not work ({code}).",
        "mic_denied": "The microphone could not be used. Record with the phone's recorder instead.",
    },
    "ru": {
        "title": "Измерение телефоном",
        "group": "Группа",
        "how": "Мост проигрывает щелчки на каждой колонке по очереди, а телефон один раз всё записывает. Пусть в комнате будет тихо.",
        "step1_live": "Нажмите «Начать запись». Не закрывайте эту страницу.",
        "step1_file": "Включите запись кнопкой ниже (или в приложении «Диктофон») и не останавливайте её.",
        "step2": "На странице моста нажмите «Проиграть последовательность».",
        "step3_live": "Держите телефон так, как подсказывает страница. В конце запись остановится и отправится сама.",
        "step3_file": "Когда последовательность закончится, остановите запись и отправьте её (если диктофон сохранил файл, выберите его ниже).",
        "device": "Держите телефон в 30–50 см от колонки, которая играет; в паузе переходите к следующей.",
        "position": "Оставайтесь там, где обычно слушаете, и держите телефон неподвижно.",
        "start": "Начать запись",
        "stop": "Остановить и отправить",
        "recording": "Запись…",
        "record_file": "Записать телефоном",
        "pick": "Или выберите запись",
        "sending": "Отправка записи…",
        "sent": "Запись измерена.",
        "waiting": "Ждём мост: нажмите там «Проиграть последовательность».",
        "lead": "Начинаем: сейчас ненадолго тишина…",
        "now": "Сейчас: только {name} ({n}/{total})",
        "next": "Дальше: {name} — переходите к ней",
        "pause": "Короткая пауза…",
        "done": "Последовательность закончилась.",
        "done_file": "Последовательность закончилась: остановите запись и отправьте её.",
        "failed": "Последовательность остановилась ({code}). Запустите её снова на странице моста.",
        "no_clicks": "Щелчки не играют: включите их на странице моста.",
        "not_measuring": "Измерение закончено, страницу можно закрыть.",
        "gone": "Эта ссылка больше не работает. Откройте новую со страницы моста.",
        "http_note": "Эта страница открыта не по HTTPS, поэтому браузер не может использовать микрофон: запишите диктофоном телефона.",
        "results": "Результаты",
        "ok": "слышно",
        "not_heard": "не слышно",
        "unstable": "нестабильно",
        "skipped": "пропущено",
        "error": "Не получилось ({code}).",
        "mic_denied": "Не удалось использовать микрофон. Запишите диктофоном телефона.",
    },
}

LANGS = tuple(TEXTS)


def pick_lang(query: str | None, accept_language: str | None) -> str:
    """The page's language: the link's ``lang`` (the bridge UI's), else the phone's Accept-Language."""
    if query in TEXTS:
        return str(query)
    for part in (accept_language or "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in TEXTS:
            return code
    return "en"


def content_security_policy(nonce: str) -> str:
    return (
        "default-src 'none'; "
        f"script-src 'nonce-{nonce}'; "
        "style-src 'unsafe-inline'; "
        "connect-src 'self'; "
        "media-src 'self' blob:; "
        "img-src 'self' data:; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )


_STYLE = """
:root{--bg:#fff;--fg:#14161a;--muted:#5b6170;--card:#f3f4f6;--line:#d6d9df;--accent:#0b63ce;--ok:#137a3a;--warn:#9a5b00;--err:#b42318}
@media (prefers-color-scheme:dark){:root{--bg:#121417;--fg:#eef0f3;--muted:#a3a9b6;--card:#1d2025;--line:#323843;--accent:#5aa2ff;--ok:#5fd18a;--warn:#f0b35a;--err:#ff7b6e}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:560px;margin:0 auto;padding:16px}
h1{font-size:1.4rem;margin:.2rem 0 .3rem}.group{color:var(--muted);margin:0 0 1rem}
ol{padding-left:1.3rem;margin:.6rem 0 1rem}li{margin:.35rem 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin:12px 0}
.now{font-size:1.35rem;font-weight:650;margin:0}.sub{color:var(--muted);margin:.3rem 0 0}
button,.file{display:block;width:100%;min-height:56px;border-radius:12px;border:0;font:inherit;font-weight:650;
 padding:14px;margin:10px 0;background:var(--accent);color:#fff;text-align:center;cursor:pointer}
button.secondary,.file.secondary{background:transparent;color:var(--accent);border:2px solid var(--accent)}
button:disabled{opacity:.5}
.file input{position:absolute;width:1px;height:1px;opacity:0}
.note{color:var(--muted);font-size:.95rem}.warn{color:var(--warn)}.err{color:var(--err)}
ul.res{list-style:none;padding:0;margin:.4rem 0}ul.res li{display:flex;justify-content:space-between;gap:12px;border-bottom:1px solid var(--line);padding:8px 0}
.st-ok{color:var(--ok)}.st-not_heard,.st-unstable{color:var(--warn)}
"""

_SCRIPT = r"""
(() => {
  const T = window.__T__, TOKEN = window.__TOKEN__;
  const api = '/api/v1/sync/capture/' + encodeURIComponent(TOKEN);
  const $ = (id) => document.getElementById(id);
  const live = window.isSecureContext && !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia) && typeof MediaRecorder !== 'undefined';
  const fmt = (s, v) => s.replace(/\{(\w+)\}/g, (_, k) => (v[k] ?? ''));
  let state = null, recorder = null, chunks = [], stream = null, sending = false, poll = null;

  $('step1').textContent = live ? T.step1_live : T.step1_file;
  $('step3').textContent = live ? T.step3_live : T.step3_file;
  $('live').hidden = !live; $('file').hidden = live;
  if (!live) $('httpnote').hidden = false;

  function say(id, text, cls) { const el = $(id); el.textContent = text || ''; el.className = cls || ''; el.hidden = !text; }

  function render() {
    if (!state) return;
    $('group').textContent = T.group + ': ' + state.group;
    $('hold').textContent = state.point === 'position' ? T.position : T.device;
    const seq = state.sequence;
    let now = '', sub = '';
    if (!state.measuring) now = T.not_measuring;
    else if (!seq) now = T.waiting;
    else if (seq.phase === 'starting' || seq.phase === 'lead') now = T.lead;
    else if (seq.phase === 'solo') { now = fmt(T.now, { name: seq.member, n: seq.index + 1, total: seq.members.length }); sub = state.point === 'position' ? T.position : T.device; }
    else if (seq.phase === 'gap') { const nx = seq.members[seq.index + 1]; now = nx ? fmt(T.next, { name: nx }) : T.pause; }
    else if (seq.phase === 'done') now = seq.uploaded || live ? T.done : T.done_file;
    else if (seq.phase === 'failed') now = fmt(T.failed, { code: seq.error || '' });
    if (state.measuring && !state.clicks_playing && (!seq || seq.phase !== 'done')) sub = T.no_clicks;
    $('now').textContent = now; say('sub', sub, 'sub');
    const res = $('res'); res.innerHTML = '';
    for (const m of state.members) {
      if (!m.status) continue;
      const li = document.createElement('li');
      const a = document.createElement('span'); a.textContent = m.name;
      const b = document.createElement('span'); b.textContent = T[m.status] || m.status; b.className = 'st-' + m.status;
      li.append(a, b); res.append(li);
    }
    $('results').hidden = !res.children.length;
    if (live && recorder && seq && seq.phase === 'done' && !seq.uploaded) stopAndSend();
  }

  async function refresh() {
    try {
      const r = await fetch(api, { cache: 'no-store' });
      if (r.status === 404) { stop(); say('msg', T.gone, 'err'); $('controls').hidden = true; return; }
      if (r.ok) { state = await r.json(); render(); }
    } catch (e) { /* the next poll tries again */ }
  }
  function stop() { if (poll) clearInterval(poll); poll = null; }

  async function upload(blob) {
    if (sending) return;
    sending = true; say('msg', T.sending, 'note');
    try {
      const r = await fetch(api + '/recording', { method: 'POST', body: blob, headers: { 'content-type': blob.type || 'application/octet-stream' } });
      if (r.ok) say('msg', T.sent, 'note');
      else { let code = r.status; try { code = (await r.json()).code || code } catch (e) {} say('msg', fmt(T.error, { code }), 'err'); }
    } catch (e) { say('msg', fmt(T.error, { code: 'network' }), 'err'); }
    finally { sending = false; refresh(); }
  }

  async function start() {
    say('msg', '');
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } }); }
    catch (e) { say('msg', T.mic_denied, 'err'); $('live').hidden = true; $('file').hidden = false; return; }
    chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    recorder.onstop = () => { const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' }); stream.getTracks().forEach((t) => t.stop()); recorder = null; upload(blob); };
    recorder.start(1000);
    $('start').hidden = true; $('stop').hidden = false; say('msg', T.recording, 'note');
  }
  function stopAndSend() { if (recorder && recorder.state !== 'inactive') { recorder.stop(); $('stop').hidden = true; $('start').hidden = false; } }

  $('start').addEventListener('click', start);
  $('stop').addEventListener('click', stopAndSend);
  for (const id of ['rec', 'pickf']) $(id).addEventListener('change', (e) => { const f = e.target.files && e.target.files[0]; if (f) upload(f); e.target.value = ''; });
  refresh(); poll = setInterval(refresh, 1000);
})();
"""


def render(token: str, lang: str, nonce: str) -> str:
    t = TEXTS[lang]
    e = html.escape
    texts = json.dumps(t, ensure_ascii=False).replace("</", "<\\/")
    tok = json.dumps(token).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="{e(lang)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<meta name="referrer" content="no-referrer">
<title>{e(t["title"])}</title>
<style>{_STYLE}</style>
</head>
<body>
<main>
<h1>{e(t["title"])}</h1>
<p class="group" id="group"></p>
<p>{e(t["how"])}</p>
<ol>
<li id="step1"></li>
<li>{e(t["step2"])}</li>
<li id="step3"></li>
</ol>
<p class="note warn" id="httpnote" hidden>{e(t["http_note"])}</p>
<div class="card" aria-live="polite">
<p class="now" id="now">…</p>
<p class="sub" id="sub" hidden></p>
<p class="note" id="hold"></p>
</div>
<div id="controls">
<div id="live" hidden>
<button type="button" id="start">{e(t["start"])}</button>
<button type="button" id="stop" class="secondary" hidden>{e(t["stop"])}</button>
</div>
<div id="file" hidden>
<label class="file">{e(t["record_file"])}<input type="file" id="rec" accept="audio/*" capture></label>
<label class="file secondary">{e(t["pick"])}<input type="file" id="pickf" accept="audio/*"></label>
</div>
</div>
<p id="msg" role="status" hidden></p>
<div class="card" id="results" hidden><strong>{e(t["results"])}</strong><ul class="res" id="res"></ul></div>
</main>
<script nonce="{e(nonce)}">window.__T__={texts};window.__TOKEN__={tok};</script>
<script nonce="{e(nonce)}">{_SCRIPT}</script>
</body>
</html>
"""


def render_gone(lang: str) -> str:
    t = TEXTS[lang]
    return (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">'
        f'<title>{html.escape(t["title"])}</title></head><body style="font:17px/1.45 system-ui,sans-serif;'
        f'max-width:560px;margin:0 auto;padding:16px"><h1>{html.escape(t["title"])}</h1>'
        f"<p>{html.escape(t['gone'])}</p></body></html>"
    )
