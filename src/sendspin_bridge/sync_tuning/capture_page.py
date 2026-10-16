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
from pathlib import Path
from typing import Any

TEXTS: dict[str, dict[str, Any]] = {
    "en": {
        "title": "Measure with this phone",
        "group": "Group",
        "how": "The bridge plays the clicks on each speaker alone, one after the other, while this phone records once. Keep the room quiet.",
        "live_note": "Press Start: the phone records, the speakers play one by one, and the recording stops and is sent by itself.",
        "recorder_note": "Press Start: your phone's recorder opens. Press record in it at once, and stop it when the countdown here reaches 0 (come back to this page to see it). The recording is then sent by itself.",
        "https_note": "To record right in the browser (it stops by itself), open the secure link. Your phone will warn about the certificate once — accept it.",
        "https_link": "Open the secure page",
        "device": "Hold the phone 30–50 cm from the speaker that plays; move to the next one in the pause.",
        "position": "Stay where you usually listen and hold the phone still.",
        "start_live": "Start",
        "start_recorder": "Start and open the recorder",
        "stop": "Stop and send now",
        "pick": "Send a recording from the phone",
        "recording": "Recording…",
        "sending": "Sending the recording…",
        "sent": "Recording sent and measured.",
        "waiting_end": "Got the recording; it is sent as soon as the sequence has finished.",
        "ready": "Ready. Press Start.",
        "need_clicks": "The clicks are not playing yet. On the computer, go through “Stop the music” and “Start the clicks”, then press Start here.",
        "lead": "Starting: every speaker is quiet for a moment…",
        "lead_recorder": "Press record in your recorder now. The speakers start in a few seconds…",
        "now": "Now: {name} alone ({n}/{total})",
        "next": "Next: {name} — move to it",
        "pause": "Short pause…",
        "stop_at_zero": "Keep recording; stop when this reaches 0.",
        "stop_now": "Stop recording now and send it.",
        "done": "The sequence has finished.",
        "done_recorder": "The sequence has finished: stop recording; it is sent when the recorder hands it back.",
        "measured": "Measured. You can look at the results on the computer.",
        "failed": "The sequence stopped ({code}). Press Start to try again.",
        "not_measuring": "The measurement is over; you can close this page.",
        "gone": "This link does not work any more. Open a new one from the bridge's page.",
        "results": "Results",
        "ok": "heard",
        "not_heard": "not heard",
        "unstable": "unstable",
        "skipped": "skipped",
        "code": "code: {code}",
        "errors": {
            "generic": "Something went wrong. Try again.",
            "network": "No connection to the bridge. Check that the phone is on the same network, then try again.",
            "mic_denied": "The microphone could not be used. Allow it for this page, or use the phone's recorder (the plain http link).",
            "clicker_stopped": "The clicks are not playing. On the computer, press “Start the clicks” (or “Start the clicks again”), then press Start here.",
            "group_playing": "The group plays something else. On the computer, stop the music first.",
            "sequence_running": "The speakers are already playing one by one; wait until it has finished.",
            "no_sequence": "The speakers have not played yet: press Start first, then record.",
            "upload_running": "A recording is being measured; wait a moment and try again.",
            "session_state": "The measurement is not taking recordings now (on the computer it may already be at the listening step).",
            "recording_invalid": "The bridge could not use this recording: {detail}",
            "sync_mute_unsupported": "A speaker cannot be muted, so the speakers cannot play one by one. Tune by ear on the computer instead.",
            "not_found": "This link does not work any more. Open a new one from the bridge's page.",
            "member_gone": "There is no speaker left to measure.",
        },
    },
    "ru": {
        "title": "Измерение телефоном",
        "group": "Группа",
        "how": "Мост проигрывает щелчки на каждой колонке по очереди, а телефон один раз всё записывает. Пусть в комнате будет тихо.",
        "live_note": "Нажмите «Начать»: телефон записывает, колонки играют по очереди, а запись останавливается и отправляется сама.",
        "recorder_note": "Нажмите «Начать»: откроется диктофон телефона. Сразу нажмите в нём запись и остановите её, когда отсчёт здесь дойдёт до 0 (вернитесь на эту страницу, чтобы его видеть). Затем запись отправится сама.",
        "https_note": "Чтобы записывать прямо в браузере (запись остановится сама), откройте защищённую ссылку. Телефон один раз предупредит о сертификате — примите его.",
        "https_link": "Открыть защищённую страницу",
        "device": "Держите телефон в 30–50 см от колонки, которая играет; в паузе переходите к следующей.",
        "position": "Оставайтесь там, где обычно слушаете, и держите телефон неподвижно.",
        "start_live": "Начать",
        "start_recorder": "Начать и открыть диктофон",
        "stop": "Остановить и отправить сейчас",
        "pick": "Отправить запись с телефона",
        "recording": "Запись…",
        "sending": "Отправка записи…",
        "sent": "Запись отправлена и измерена.",
        "waiting_end": "Запись получена; она отправится, как только последовательность закончится.",
        "ready": "Всё готово. Нажмите «Начать».",
        "need_clicks": "Щелчки ещё не играют. На компьютере пройдите шаги «Остановите музыку» и «Включите щелчки», затем нажмите «Начать» здесь.",
        "lead": "Начинаем: все колонки ненадолго замолкают…",
        "lead_recorder": "Нажмите запись в диктофоне сейчас. Колонки заиграют через несколько секунд…",
        "now": "Сейчас: только {name} ({n}/{total})",
        "next": "Дальше: {name} — переходите к ней",
        "pause": "Короткая пауза…",
        "stop_at_zero": "Продолжайте запись; остановите её, когда здесь будет 0.",
        "stop_now": "Остановите запись сейчас и отправьте её.",
        "done": "Последовательность закончилась.",
        "done_recorder": "Последовательность закончилась: остановите запись; она отправится, когда диктофон её вернёт.",
        "measured": "Измерено. Результаты видны на компьютере.",
        "failed": "Последовательность остановилась ({code}). Нажмите «Начать», чтобы попробовать снова.",
        "not_measuring": "Измерение закончено, страницу можно закрыть.",
        "gone": "Эта ссылка больше не работает. Откройте новую со страницы моста.",
        "results": "Результаты",
        "ok": "слышно",
        "not_heard": "не слышно",
        "unstable": "нестабильно",
        "skipped": "пропущено",
        "code": "код: {code}",
        "errors": {
            "generic": "Что-то пошло не так. Попробуйте ещё раз.",
            "network": "Нет связи с мостом. Проверьте, что телефон в той же сети, и попробуйте ещё раз.",
            "mic_denied": "Не удалось использовать микрофон. Разрешите его для этой страницы или воспользуйтесь диктофоном (обычная http-ссылка).",
            "clicker_stopped": "Щелчки не играют. На компьютере нажмите «Включить щелчки» (или «Включить щелчки снова»), затем нажмите «Начать» здесь.",
            "group_playing": "Группа играет что-то другое. Сначала остановите музыку на компьютере.",
            "sequence_running": "Колонки уже играют по очереди; дождитесь окончания.",
            "no_sequence": "Колонки ещё не играли: сначала нажмите «Начать», затем записывайте.",
            "upload_running": "Запись уже измеряется; подождите немного и попробуйте снова.",
            "session_state": "Измерение сейчас не принимает записи (на компьютере, возможно, уже идёт прослушивание).",
            "recording_invalid": "Мост не смог использовать эту запись: {detail}",
            "sync_mute_unsupported": "Одну из колонок нельзя заглушить, поэтому колонки не могут играть по очереди. Настройте на слух на компьютере.",
            "not_found": "Эта ссылка больше не работает. Откройте новую со страницы моста.",
            "member_gone": "Не осталось колонок для измерения.",
        },
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
*{box-sizing:border-box}[hidden]{display:none!important}html{-webkit-text-size-adjust:100%}
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
.count{font-size:3.2rem;font-weight:750;font-variant-numeric:tabular-nums;margin:.2rem 0;text-align:center}
.err p{margin:.2rem 0}.err #err{color:var(--err)}.code{color:var(--muted);font-size:.8rem}
"""

_SCRIPT = (Path(__file__).with_name("capture_page.js")).read_text(encoding="utf-8")


def render(token: str, lang: str, nonce: str, https_url: str | None = None) -> str:
    """The page; *https_url* (the same page on the HTTPS capture server) is offered on plain HTTP."""
    t = TEXTS[lang]
    e = html.escape
    texts = json.dumps(t, ensure_ascii=False).replace("</", "<\\/")
    tok = json.dumps(token).replace("</", "<\\/")
    https = (
        f'<p class="note" id="https-note" hidden>{e(t["https_note"])} '
        f'<a id="https-link" href="{e(https_url)}">{e(t["https_link"])}</a></p>'
        if https_url
        else ""
    )
    return f"""<!doctype html>
<html lang="{e(lang)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<meta name="referrer" content="no-referrer">
<link rel="icon" href="data:,">
<title>{e(t["title"])}</title>
<style>{_STYLE}</style>
</head>
<body>
<main>
<h1>{e(t["title"])}</h1>
<p class="group" id="group"></p>
<p>{e(t["how"])}</p>
<p class="note" id="mode-note"></p>
{https}
<div class="card" aria-live="polite">
<p class="now" id="now">…</p>
<p class="count" id="count" hidden></p>
<p class="sub" id="sub"></p>
</div>
<div id="controls">
<button type="button" id="start"></button>
<button type="button" id="stop" class="secondary" hidden>{e(t["stop"])}</button>
<input type="file" id="rec" accept="audio/*" capture hidden>
<label class="file secondary" id="pick-wrap" hidden>{e(t["pick"])}<input type="file" id="pick" accept="audio/*"></label>
</div>
<p id="msg" role="status"></p>
<div class="card err" id="errbox" role="alert" hidden><p id="err"></p><p class="code" id="errcode"></p></div>
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
