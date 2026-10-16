// The phone capture page (served inline by capture_page.py under a CSP nonce).
//
// Two ways to record, chosen once and deterministically:
//  - live: the page is in a secure context (HTTPS or localhost) and the browser has getUserMedia and
//    MediaRecorder: "Start" opens the microphone, asks the bridge to play the sequence, and the
//    recording stops and is sent by itself when the sequence has finished.
//  - recorder: anything else (plain http on the LAN): "Start" opens the phone's own recorder through
//    <input type=file capture> and asks the bridge to play the sequence after a longer lead-in; a big
//    countdown says when to stop; the file is sent when the recorder hands it back (and the sequence
//    has finished).
// Every error is said in words; its code only in small print.
(() => {
  const T = window.__T__
  const TOKEN = window.__TOKEN__
  const api = '/api/v1/sync/capture/' + encodeURIComponent(TOKEN)
  const $ = (id) => document.getElementById(id)
  const fmt = (s, v) => String(s).replace(/\{(\w+)\}/g, (_, k) => (v[k] ?? ''))
  const clock = (sec) => {
    const s = Math.max(0, Math.ceil(sec))
    return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0')
  }
  const mode =
    window.isSecureContext &&
    !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia) &&
    typeof window.MediaRecorder !== 'undefined'
      ? 'live'
      : 'recorder'
  document.body.dataset.mode = mode

  let state = null
  let recorder = null
  let chunks = []
  let stream = null
  let sending = false
  let started = null // { at: ms, total: s } when this phone started a sequence
  let pendingFile = null // a recording handed back before the sequence finished
  let uploadedFor = null // the sequence object this phone already sent a recording for
  let gone = false

  function show(id, on) {
    const el = $(id)
    if (el) el.hidden = !on
  }
  function text(id, value) {
    const el = $(id)
    if (el) el.textContent = value || ''
  }
  function error(code, detail) {
    const known = T.errors[code]
    text('err', known ? fmt(known, { detail: detail || '' }) : detail || T.errors.generic)
    text('errcode', code ? fmt(T.code, { code }) : '')
    show('errbox', true)
  }
  function clearError() {
    show('errbox', false)
  }

  function sequenceTotal(seq) {
    const n = seq.members.length
    return seq.lead_s + n * seq.solo_s + Math.max(0, n - 1) * seq.gap_s
  }
  const running = (seq) => !!seq && ['starting', 'lead', 'solo', 'gap'].includes(seq.phase)

  text('mode-note', mode === 'live' ? T.live_note : T.recorder_note)
  show('https-note', mode === 'recorder' && !!$('https-link'))
  text('start', mode === 'live' ? T.start_live : T.start_recorder)

  function render() {
    if (!state) return
    text('group', T.group + ': ' + state.group)
    const seq = state.sequence
    let now = ''
    let sub = ''
    let count = ''
    const canStart = state.measuring && state.clicks_playing && !running(seq) && !recorder && !sending
    if (!state.measuring) now = T.not_measuring
    else if (running(seq)) {
      if (seq.phase === 'starting' || seq.phase === 'lead') now = mode === 'recorder' ? T.lead_recorder : T.lead
      else if (seq.phase === 'solo') now = fmt(T.now, { name: seq.member, n: seq.index + 1, total: seq.members.length })
      else {
        const next = seq.members[seq.index + 1]
        now = next ? fmt(T.next, { name: next }) : T.pause
      }
      sub = state.point === 'position' ? T.position : T.device
    } else if (seq && seq.phase === 'done') {
      now = seq.uploaded ? T.measured : mode === 'recorder' ? T.done_recorder : T.done
    } else if (seq && seq.phase === 'failed') now = fmt(T.failed, { code: seq.error || '' })
    else if (!state.clicks_playing) now = T.need_clicks
    else now = T.ready
    if (started) {
      const left = started.total - (Date.now() - started.at) / 1000
      if (left > -2 && (running(seq) || left > 0)) {
        count = clock(left)
        if (mode === 'recorder') sub = left > 0 ? T.stop_at_zero : T.stop_now
      }
    }
    text('now', now)
    text('sub', sub)
    text('count', count)
    show('count', !!count)
    const startBtn = $('start')
    if (startBtn) startBtn.disabled = !canStart
    show('start', !recorder)
    show('stop', mode === 'live' && !!recorder)
    show('pick-wrap', mode === 'recorder' || !!(seq && seq.phase === 'done' && !seq.uploaded))

    const res = $('res')
    res.replaceChildren()
    for (const m of state.members) {
      if (!m.status) continue
      const li = document.createElement('li')
      const a = document.createElement('span')
      a.textContent = m.name
      const b = document.createElement('span')
      b.textContent = T[m.status] || m.status
      b.className = 'st-' + m.status
      li.append(a, b)
      res.append(li)
    }
    show('results', res.children.length > 0)

    if (seq && seq.phase === 'done' && !seq.uploaded && uploadedFor !== seq.lead_s + ':' + seq.members.join('|')) {
      if (mode === 'live' && recorder) stopAndSend()
      else if (pendingFile) {
        const f = pendingFile
        pendingFile = null
        upload(f)
      }
    }
    if (seq && seq.phase === 'failed' && recorder) cancelRecording()
  }

  async function refresh() {
    try {
      const r = await fetch(api, { cache: 'no-store' })
      if (r.status === 404) {
        gone = true
        stopPolling()
        cancelRecording()
        text('now', T.gone)
        show('controls', false)
        return
      }
      if (r.ok) {
        state = await r.json()
        render()
      }
    } catch (e) {
      /* the next poll tries again */
    }
  }
  let poll = null
  function stopPolling() {
    if (poll) clearInterval(poll)
    poll = null
  }

  async function problemOf(r) {
    try {
      const p = await r.json()
      return { code: p.code || String(r.status), detail: p.detail || '' }
    } catch (e) {
      return { code: String(r.status), detail: '' }
    }
  }

  async function startSequence() {
    const r = await fetch(api + '/start', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ mode }),
    })
    if (!r.ok) {
      const p = await problemOf(r)
      error(p.code, p.detail)
      return false
    }
    state = await r.json()
    if (state.sequence) started = { at: Date.now(), total: sequenceTotal(state.sequence) }
    render()
    return true
  }

  async function upload(blob) {
    if (sending || gone) return
    sending = true
    clearError()
    text('msg', T.sending)
    try {
      const r = await fetch(api + '/recording', {
        method: 'POST',
        body: blob,
        headers: { 'content-type': blob.type || 'application/octet-stream' },
      })
      if (r.ok) {
        text('msg', T.sent)
        const seq = state && state.sequence
        if (seq) uploadedFor = seq.lead_s + ':' + seq.members.join('|')
        started = null
      } else {
        const p = await problemOf(r)
        text('msg', '')
        error(p.code, p.detail)
      }
    } catch (e) {
      text('msg', '')
      error('network', '')
    } finally {
      sending = false
      refresh()
    }
  }

  async function startLive() {
    clearError()
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      })
    } catch (e) {
      error('mic_denied', '')
      return
    }
    chunks = []
    recorder = new window.MediaRecorder(stream)
    recorder.ondataavailable = (e) => {
      if (e.data && e.data.size) chunks.push(e.data)
    }
    recorder.onstop = () => {
      const send = recorder && recorder.sendOnStop
      const blob = new Blob(chunks, { type: (recorder && recorder.mimeType) || 'audio/webm' })
      if (stream) stream.getTracks().forEach((t) => t.stop())
      recorder = null
      render()
      if (send) upload(blob)
    }
    recorder.start(1000)
    text('msg', T.recording)
    if (!(await startSequence())) cancelRecording()
  }

  function stopAndSend() {
    if (recorder && recorder.state !== 'inactive') {
      recorder.sendOnStop = true
      recorder.stop()
    }
  }
  function cancelRecording() {
    if (recorder && recorder.state !== 'inactive') {
      recorder.sendOnStop = false
      recorder.stop()
    }
    text('msg', '')
  }

  function startRecorder() {
    clearError()
    // Opened inside the tap (the browser allows it only then); the bridge waits RECORDER_LEAD_S.
    const input = $('rec')
    if (input) input.click()
    startSequence()
  }

  function onFile(e) {
    const f = e.target.files && e.target.files[0]
    e.target.value = ''
    if (!f) return
    const seq = state && state.sequence
    if (seq && running(seq)) {
      pendingFile = f // sent once the sequence has finished
      text('msg', T.waiting_end)
    } else upload(f)
  }

  $('start').addEventListener('click', () => (mode === 'live' ? startLive() : startRecorder()))
  $('stop').addEventListener('click', stopAndSend)
  for (const id of ['rec', 'pick']) {
    const el = $(id)
    if (el) el.addEventListener('change', onFile)
  }
  setInterval(() => {
    if (started) render()
  }, 250)
  refresh()
  poll = setInterval(refresh, 1000)
  window.__capture__ = { mode, refresh, render }
})()
