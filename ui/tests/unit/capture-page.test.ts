import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * The phone capture page's script (src/sendspin_bridge/sync_tuning/capture_page.js), run in happy-dom
 * against a stubbed bridge: which way it records is decided once and deterministically, the phone
 * starts the sequence itself, and every error is said in words with the code in small print.
 */
const SCRIPT = readFileSync(resolve(__dirname, '../../../src/sendspin_bridge/sync_tuning/capture_page.js'), 'utf8')
const T = new Proxy(
  { errors: { generic: 'GENERIC', clicker_stopped: 'Start the clicks on the computer', recording_invalid: 'Unusable: {detail}' }, code: 'code: {code}', now: 'Now {name} ({n}/{total})' },
  { get: (t, k: string) => (k in t ? (t as Record<string, unknown>)[k] : k) },
)
const DOM = `<p id="group"></p><p id="mode-note"></p><p id="now"></p><p id="count" hidden></p><p id="sub"></p>
<div id="controls"><button id="start"></button><button id="stop" hidden></button><input type="file" id="rec" hidden>
<label id="pick-wrap" hidden><input type="file" id="pick"></label></div><p id="msg"></p>
<div id="errbox" hidden><p id="err"></p><p id="errcode"></p></div><div id="results" hidden><ul id="res"></ul></div>`

const SEQ = { members: ['Kitchen', 'Hall'], phase: 'starting', index: -1, member: null, error: null, error_member: null, solo_s: 7, gap_s: 5, lead_s: 3, uploaded: false }
let state: Record<string, unknown>
let calls: { url: string; init?: RequestInit }[]
let answers: Record<string, () => Response>

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

function boot({ secure }: { secure: boolean }) {
  document.body.innerHTML = DOM
  ;(window as unknown as Record<string, unknown>).__T__ = T
  ;(window as unknown as Record<string, unknown>).__TOKEN__ = 'tok'
  Object.defineProperty(window, 'isSecureContext', { value: secure, configurable: true })
  // eslint-disable-next-line no-new-func
  new Function(SCRIPT)()
}

async function settle() {
  for (let i = 0; i < 5; i++) await vi.advanceTimersByTimeAsync(0)
}

describe('phone capture page', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    calls = []
    state = { group: 'Downstairs', point: 'device', measuring: true, clicks_playing: true, members: [], sequence: null }
    answers = {}
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init })
      const key = `${init?.method ?? 'GET'} ${url}`
      if (answers[key]) return answers[key]!()
      return json(state)
    }))
  })
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

  it('on plain http it uses the phone recorder: Start opens it and asks the bridge for a long lead-in', async () => {
    Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia: vi.fn() }, configurable: true })
    vi.stubGlobal('MediaRecorder', class {})
    boot({ secure: false })
    await settle()
    expect(document.body.dataset.mode).toBe('recorder')
    const rec = document.getElementById('rec') as HTMLInputElement
    const opened = vi.spyOn(rec, 'click')
    answers['POST /api/v1/sync/capture/tok/start'] = () => json({ ...state, sequence: { ...SEQ, lead_s: 10 } }, 202)
    document.getElementById('start')!.click()
    await settle()
    expect(opened).toHaveBeenCalled()
    const start = calls.find((c) => c.url.endsWith('/start'))!
    expect(JSON.parse(String(start.init!.body))).toEqual({ mode: 'recorder' })
    // the countdown to "stop recording" is the whole sequence: 10 + 2×7 + 1×5 = 29 s
    expect(document.getElementById('count')!.textContent).toBe('0:29')
    expect(document.getElementById('sub')!.textContent).toBe('stop_at_zero')
  })

  it('in a secure context it records live and sends by itself when the sequence has finished', async () => {
    const stop = vi.fn()
    class Recorder {
      state = 'recording'; mimeType = 'audio/webm'; ondataavailable?: (e: { data: Blob }) => void; onstop?: () => void; sendOnStop?: boolean
      start() { this.ondataavailable?.({ data: new Blob(['x']) }) }
      stop() { stop(); this.state = 'inactive'; this.onstop?.() }
    }
    vi.stubGlobal('MediaRecorder', Recorder)
    Object.defineProperty(navigator, 'mediaDevices', {
      value: { getUserMedia: vi.fn(async () => ({ getTracks: () => [{ stop() {} }] })) }, configurable: true,
    })
    boot({ secure: true })
    await settle()
    expect(document.body.dataset.mode).toBe('live')
    answers['POST /api/v1/sync/capture/tok/start'] = () => json({ ...state, sequence: SEQ }, 202)
    document.getElementById('start')!.click()
    await settle()
    expect(JSON.parse(String(calls.find((c) => c.url.endsWith('/start'))!.init!.body))).toEqual({ mode: 'live' })
    expect(stop).not.toHaveBeenCalled()
    state = { ...state, sequence: { ...SEQ, phase: 'done' } }
    answers['POST /api/v1/sync/capture/tok/recording'] = () => json({ results: [] })
    await vi.advanceTimersByTimeAsync(1000)
    await settle()
    expect(stop).toHaveBeenCalled()
    expect(calls.some((c) => c.url.endsWith('/recording'))).toBe(true)
  })

  it('says in words what to do when the bridge refuses, with the code in small print', async () => {
    boot({ secure: false })
    await settle()
    answers['POST /api/v1/sync/capture/tok/start'] = () => json({ code: 'clicker_stopped', detail: 'start the clicker first' }, 409)
    document.getElementById('start')!.click()
    await settle()
    expect(document.getElementById('err')!.textContent).toBe('Start the clicks on the computer')
    expect(document.getElementById('errcode')!.textContent).toBe('code: clicker_stopped')
    expect(document.getElementById('errbox')!.hidden).toBe(false)
  })

  it('shows the reason an upload was refused', async () => {
    boot({ secure: false })
    await settle()
    state = { ...state, sequence: { ...SEQ, phase: 'done' } }
    answers['POST /api/v1/sync/capture/tok/recording'] = () =>
      json({ code: 'recording_invalid', detail: 'AMR-NB (phone-call quality) cuts off the clicks' }, 400)
    const pick = document.getElementById('pick') as HTMLInputElement
    Object.defineProperty(pick, 'files', { value: [new File(['x'], 'r.amr', { type: 'audio/amr' })], configurable: true })
    pick.dispatchEvent(new Event('change'))
    await settle()
    expect(document.getElementById('err')!.textContent).toBe('Unusable: AMR-NB (phone-call quality) cuts off the clicks')
  })

  it('does not offer Start before the clicks play, and says what to do on the computer', async () => {
    state = { ...state, clicks_playing: false }
    boot({ secure: false })
    await settle()
    expect((document.getElementById('start') as HTMLButtonElement).disabled).toBe(true)
    expect(document.getElementById('now')!.textContent).toBe('need_clicks')
  })
})
