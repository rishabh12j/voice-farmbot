"""EdgeSpeech workbench — LLM behaviour-tree pipeline.

Single pipeline:

  STT (faster-whisper / vosk / moonshine)
    → AICore (Ollama-backed intent classifier)
    → deterministic Python tree builder
    → tree rendering + extracted FarmBot commands + LLM responses
    → TTS confirmation (piper / kokoro)

The behaviour tree is **constructed only**, never executed. This is a workbench:
no robot, no ROS2 publish, no FarmBot REST hits, no wait blocks. Round-trip
stays bounded by Ollama's classification call.

Run::

    python -m growmate_voice.stt_test                    # http://127.0.0.1:7870
    python -m growmate_voice.stt_test --port 8000
"""

from __future__ import annotations

import argparse
import base64
import sys
import traceback
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from . import bt_bridge
from .edgespeech.audio_utils import (
    SAMPLE_RATE,
    audio_info,
    audio_to_wav_bytes,
    load_wav_from_bytes,
)
from .edgespeech.stt import load_stt
from .edgespeech.tts import load_tts
from .logger import log, log_path


_STT_CACHE: Dict[str, Any] = {}
_TTS_CACHE: Dict[str, Any] = {}
_DEFAULT_MODEL = "gemma3:4b"


def _get_stt(name: str) -> Any:
    if name not in _STT_CACHE:
        _STT_CACHE[name] = load_stt(name)
    return _STT_CACHE[name]


def _get_tts(name: str) -> Any:
    if name not in _TTS_CACHE:
        _TTS_CACHE[name] = load_tts(name)
    return _TTS_CACHE[name]


app = FastAPI(title="EdgeSpeech Workbench")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML


@app.get("/api/bt/status")
def api_bt_status(model: str = _DEFAULT_MODEL) -> Dict[str, Any]:
    return bt_bridge.status(model)


def _classify(text: str, model: str) -> Dict[str, Any]:
    """Run the BT pipeline on ``text`` and return a UI-ready dict."""
    tree, ascii_view, err = bt_bridge.classify_and_render(text, model=model)
    if err:
        return {
            "tree_label": None,
            "spoken_phrase": "",
            "tree_ascii": "",
            "tree_json": None,
            "robot_commands": [],
            "responses": [],
            "node_counts": {},
            "bt_error": err,
        }
    label = (tree or {}).get("label", "(unlabeled)")
    responses = bt_bridge.extract_responses(tree)
    return {
        "tree_label": label,
        "spoken_phrase": "; ".join(responses),
        "tree_ascii": ascii_view,
        "tree_json": tree,
        "robot_commands": bt_bridge.extract_robot_commands(tree),
        "responses": responses,
        "node_counts": bt_bridge.extract_node_summary(tree),
        "bt_error": "",
    }


def _maybe_tts(
    enable: bool,
    backend_name: str,
    phrase: str,
    pipeline_log: List[str],
) -> tuple[str, Optional[str]]:
    if not enable or backend_name == "none" or not phrase:
        return "", None
    try:
        backend = _get_tts(backend_name)
        if not backend.is_available():
            pipeline_log.append(f"⚠ TTS '{backend_name}' not available")
            return "", None
        pipeline_log.append(f"🔊 TTS ({backend_name}): '{phrase}'")
        tts_np, tts_sr = backend.synthesise(phrase)
        wav_out = audio_to_wav_bytes(tts_np, sample_rate=tts_sr)
        return phrase, base64.b64encode(wav_out).decode("ascii")
    except Exception as exc:  # noqa: BLE001
        pipeline_log.append(f"⚠ TTS error: {exc}")
        return "", None


@app.post("/api/voice")
async def api_voice(
    audio: UploadFile = File(...),
    stt: str = Form("whisper"),
    tts: str = Form("none"),
    enable_tts: str = Form("true"),
    model: str = Form(_DEFAULT_MODEL),
) -> Any:
    pipeline_log: List[str] = []
    try:
        wav_bytes = await audio.read()
        pipeline_log.append(f"📥 Received {len(wav_bytes)} bytes")

        np_audio = load_wav_from_bytes(wav_bytes)
        duration, peak = audio_info(np_audio, SAMPLE_RATE)
        pipeline_log.append(f"🎧 {duration:.2f}s @ {SAMPLE_RATE} Hz | peak={peak:.3f}")

        stt_backend = _get_stt(stt)
        if not stt_backend.is_available():
            pipeline_log.append(f"⚠ STT '{stt}' not available")
            return JSONResponse(status_code=400, content={
                "error": f"STT backend '{stt}' is not available.",
                "log": "\n".join(pipeline_log),
            })

        pipeline_log.append(f"🎙 STT: {stt_backend.name}")
        transcript, latency_ms = stt_backend.transcribe(np_audio, sample_rate=SAMPLE_RATE)
        pipeline_log.append(f"📝 '{transcript}' ({latency_ms:.1f} ms)")

        pipeline_log.append(f"🌳 BT (model={model})")
        classification = _classify(transcript, model)
        if classification["bt_error"]:
            pipeline_log.append(f"⚠ BT error: {classification['bt_error']}")
        else:
            pipeline_log.append(
                f"🌳 {classification['tree_label']} "
                f"({len(classification['robot_commands'])} robot ops)"
            )

        spoken, b64 = _maybe_tts(
            enable_tts.lower() == "true",
            tts,
            classification["spoken_phrase"],
            pipeline_log,
        )

        pipeline_log.append("✅ Done")
        return JSONResponse({
            "result": {
                "raw_transcript": transcript,
                "stt_latency_ms": round(latency_ms, 1),
                "stt_backend": stt_backend.name,
                "audio_seconds": round(duration, 3),
                "audio_peak": round(peak, 3),
                "tts_spoken": spoken,
                **classification,
            },
            "log": "\n".join(pipeline_log),
            "tts_audio_b64": b64,
        })
    except Exception as exc:  # noqa: BLE001
        pipeline_log.append(f"❌ Error: {exc}")
        log.exception("Workbench voice pipeline error")
        return JSONResponse(status_code=500, content={
            "error": str(exc),
            "log": "\n".join(pipeline_log),
            "trace": traceback.format_exc(),
        })


@app.post("/api/text")
def api_text(
    text: str = Form(...),
    model: str = Form(_DEFAULT_MODEL),
    tts: str = Form("none"),
    enable_tts: str = Form("true"),
) -> Any:
    """Skip STT — useful when iterating on the LLM prompt or BT shape."""
    pipeline_log: List[str] = [f"📝 (text-mode) '{text}'", f"🌳 BT (model={model})"]
    try:
        classification = _classify(text, model)
        if classification["bt_error"]:
            pipeline_log.append(f"⚠ BT error: {classification['bt_error']}")
        else:
            pipeline_log.append(
                f"🌳 {classification['tree_label']} "
                f"({len(classification['robot_commands'])} robot ops)"
            )

        spoken, b64 = _maybe_tts(
            enable_tts.lower() == "true",
            tts,
            classification["spoken_phrase"],
            pipeline_log,
        )

        pipeline_log.append("✅ Done")
        return JSONResponse({
            "result": {
                "raw_transcript": text,
                "stt_latency_ms": 0,
                "stt_backend": "(text)",
                "tts_spoken": spoken,
                **classification,
            },
            "log": "\n".join(pipeline_log),
            "tts_audio_b64": b64,
        })
    except Exception as exc:  # noqa: BLE001
        log.exception("Workbench text pipeline error")
        return JSONResponse(status_code=500, content={
            "error": str(exc),
            "log": "\n".join(pipeline_log),
            "trace": traceback.format_exc(),
        })


# --------------------------------------------------------------------------- html
INDEX_HTML = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<title>🔬 EdgeSpeech Workbench</title>
<style>
  :root {
    --bg: #1a1a1a; --panel: #2d2d2d;
    --fg: #e8e8e8; --muted: #999;
    --green: #6fcf97; --green-dk: #2d6a4f;
    --red: #b71c1c; --red-dk: #7f0000;
    --blue-lt: #90caf9; --blue-dk: #1e3a5f;
    --purple: #c48cff;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--fg);
    font-family: system-ui, -apple-system, Segoe UI, sans-serif;
  }
  .wrap { max-width: 880px; margin: 0 auto; padding: 28px 18px 48px; }
  h1 {
    text-align: center; color: var(--green);
    font-size: 2.1em; font-weight: 900; letter-spacing: 1px;
    margin: 4px 0 2px;
  }
  .sub {
    text-align: center; color: var(--muted); font-size: 1em;
    margin-bottom: 18px; letter-spacing: 0.5px;
  }
  .tag {
    display: inline-block; background: #2a1a3a; color: var(--purple);
    padding: 2px 8px; border-radius: 8px; font-size: 0.75em;
    margin-left: 6px; letter-spacing: 0.5px;
  }

  .voice-hero { text-align: center; padding: 14px 12px 4px; }
  .voice-hero-title {
    color: var(--green); font-size: 1.4em; font-weight: 800;
    margin-bottom: 4px;
  }
  .voice-hero-hint { color: var(--muted); font-size: 0.95em; line-height: 1.5; }

  .row { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; margin: 10px 0; }
  .voice-row label { font-size: 0.95em; color: #ccc; }
  .voice-row select, .voice-row input[type=text] {
    background: var(--panel); color: var(--fg); border: 1px solid #404040;
    padding: 8px 10px; border-radius: 8px; font-size: 0.95em;
  }
  .voice-row input[type=text] { width: 130px; }

  .bt-status-card {
    background: var(--panel); border: 1px solid #333;
    padding: 10px 14px; border-radius: 10px;
    color: var(--muted); font-family: ui-monospace, monospace;
    font-size: 0.86em; margin: 12px 0; line-height: 1.5;
  }
  .bt-status-card.ok    { border-color: var(--green-dk); color: var(--green); }
  .bt-status-card.warn  { border-color: #6a4a1a; color: #ffcc80; }
  .bt-status-card.error { border-color: var(--red-dk); color: #ef9a9a; }

  .rec-wrap {
    display: flex; flex-direction: column; align-items: center;
    gap: 10px; margin: 14px 0 18px;
  }
  .rec-btn-big {
    background: linear-gradient(180deg, #2d6a4f 0%, #1b4332 100%);
    color: #fff; border: 3px solid var(--green-dk);
    width: 100%; max-width: 420px; min-height: 120px;
    border-radius: 24px; font-size: 1.4em; font-weight: 800;
    letter-spacing: 1px; cursor: pointer;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 6px; transition: transform 0.1s, box-shadow 0.1s;
    box-shadow: 0 8px 24px rgba(111, 207, 151, 0.3);
  }
  .rec-btn-big:hover { transform: translateY(-2px); box-shadow: 0 10px 28px rgba(111, 207, 151, 0.4); }
  .rec-btn-big.recording {
    background: linear-gradient(180deg, #c62828 0%, #7a0000 100%);
    border-color: var(--red-dk);
    animation: pulse 1.2s ease-in-out infinite;
  }
  .rec-btn-big .mic-icon { font-size: 2.2em; }
  @keyframes pulse {
    0%, 100% { box-shadow: 0 8px 24px rgba(198, 40, 40, 0.4); }
    50%      { box-shadow: 0 8px 34px rgba(198, 40, 40, 0.8); }
  }
  .mic-status { color: var(--muted); font-size: 0.95em; }

  .text-row {
    display: flex; gap: 10px; align-items: center;
    margin: 6px 0 16px;
  }
  .text-row input {
    flex: 1; background: var(--panel); color: var(--fg);
    border: 1px solid #404040; padding: 12px 14px;
    border-radius: 10px; font-size: 1em;
  }
  .text-row button {
    background: #2a1a3a; color: var(--purple); border: 2px solid #7b46c1;
    padding: 12px 18px; border-radius: 10px; font-weight: 700; cursor: pointer;
  }
  .text-row button:hover { background: #3a2a4a; }

  pre.log {
    background: #111; color: #cfe0ff; padding: 12px 14px;
    border-radius: 10px; font-family: ui-monospace, monospace;
    font-size: 0.86em; white-space: pre-wrap; min-height: 3em;
    border: 1px solid #222;
  }
  pre.result {
    background: #0d1a2e; color: var(--blue-lt); padding: 12px 14px;
    border-radius: 10px; font-family: ui-monospace, monospace;
    font-size: 0.88em; white-space: pre-wrap; border: 1px solid var(--blue-dk);
  }
  pre.tree {
    background: #1a0f24; color: var(--purple); padding: 12px 14px;
    border-radius: 10px; font-family: ui-monospace, monospace;
    font-size: 0.92em; white-space: pre; border: 1px solid #4a2a6a;
    overflow-x: auto;
  }
  pre.tree.empty { color: var(--muted); font-style: italic; }

  .badges { display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0 8px; }
  .badge {
    background: #1b4332; color: #c8efd6; padding: 3px 9px;
    border-radius: 8px; font-size: 0.8em; font-family: ui-monospace, monospace;
  }
  .badge.warn { background: #3b1f1f; color: #ffb380; }

  audio { width: 100%; margin-top: 8px; }

  .section-h {
    color: var(--blue-lt); font-weight: 700; font-size: 1em;
    margin: 16px 0 6px 4px; letter-spacing: 0.3px;
  }
  .section-h.tree { color: var(--purple); }
</style>
</head>
<body>
<div class="wrap">

  <h1>🔬 EdgeSpeech Workbench</h1>
  <div class="sub">LLM-driven behaviour-tree pipeline<span class="tag">BT only</span></div>

  <div class="voice-hero">
    <div class="voice-hero-title">Speak or type a command</div>
    <div class="voice-hero-hint">
      Robot: &ldquo;water the tomatoes and go home&rdquo;, &ldquo;move to the herbs&rdquo;, &ldquo;take a photo&rdquo;<br>
      Knowledge: &ldquo;when should I plant basil&rdquo;, &ldquo;what vegetables grow well in spring&rdquo;
    </div>
  </div>

  <div class="bt-status-card" id="btStatus">BT status: …</div>

  <div class="row voice-row" style="justify-content: center;">
    <label>STT:
      <select id="stt">
        <option value="whisper">whisper</option>
        <option value="vosk">vosk</option>
        <option value="moonshine">moonshine</option>
      </select>
    </label>
    <label>TTS:
      <select id="tts">
        <option value="none">none</option>
        <option value="piper">piper</option>
        <option value="kokoro">kokoro</option>
      </select>
    </label>
    <label><input type="checkbox" id="enableTts" checked/> Enable TTS</label>
    <label>Model: <input type="text" id="model" value="gemma3:4b"/></label>
  </div>

  <div class="rec-wrap">
    <button id="recBtn" class="rec-btn-big">
      <span class="mic-icon">🎙</span>
      <span id="recLabel">Tap to record</span>
    </button>
    <div class="mic-status" id="micStatus">Idle</div>
  </div>

  <div class="text-row">
    <input id="textIn" placeholder='Type a transcript and "Run" to skip STT (faster prompt iteration)' />
    <button onclick="runText()">Run</button>
  </div>

  <div class="section-h tree">🌳 Behaviour Tree</div>
  <pre class="tree empty" id="treeOut">(no command yet)</pre>

  <div class="section-h">Robot commands the tree would emit</div>
  <div class="badges" id="cmdBadges"><span class="badge warn">none</span></div>

  <div class="section-h">Node breakdown</div>
  <div class="badges" id="nodeBadges"><span class="badge warn">none</span></div>

  <div class="section-h">Result</div>
  <pre class="result" id="voiceResult">(no result yet)</pre>

  <div class="section-h">Pipeline log</div>
  <pre class="log" id="voiceLog">—</pre>

  <audio id="ttsAudio" controls></audio>

</div>

<script>
const TARGET_SR = 16000;

/* ============ BT status ============ */
async function refreshBtStatus() {
  const model = document.getElementById('model').value || 'gemma3:4b';
  const card = document.getElementById('btStatus');
  card.classList.remove('ok', 'warn', 'error');
  try {
    const r = await fetch('/api/bt/status?model=' + encodeURIComponent(model));
    const s = await r.json();
    if (!s.available) {
      card.textContent = '⚠ BT unavailable: ' + s.error;
      card.classList.add('error');
    } else if (!s.ollama_reachable) {
      card.textContent = `⚠ Loaded but Ollama unreachable for model "${s.model}". Run: ollama serve && ollama pull ${s.model}`;
      card.classList.add('warn');
    } else {
      card.textContent =
        `✓ Ready  ·  source=${s.source}  ·  model=${s.model}  ·  config=${s.config_path}`;
      card.classList.add('ok');
    }
  } catch (e) {
    card.textContent = '⚠ status check failed: ' + e.message;
    card.classList.add('error');
  }
}
refreshBtStatus();
document.getElementById('model').addEventListener('change', refreshBtStatus);

/* ============ result rendering ============ */
function renderResult(data) {
  const result = data.result || data;
  document.getElementById('voiceResult').textContent = JSON.stringify(result, null, 2);
  document.getElementById('voiceLog').textContent = data.log || '(no log)';

  const treeEl = document.getElementById('treeOut');
  if (result.bt_error) {
    treeEl.classList.add('empty');
    treeEl.textContent = '⚠ ' + result.bt_error;
  } else if (result.tree_ascii) {
    treeEl.classList.remove('empty');
    treeEl.textContent = result.tree_ascii;
  } else {
    treeEl.classList.add('empty');
    treeEl.textContent = '(no tree returned)';
  }

  const badges = document.getElementById('cmdBadges');
  const cmds = result.robot_commands || [];
  badges.innerHTML = cmds.length
    ? cmds.map(c => `<span class="badge">${c}</span>`).join('')
    : '<span class="badge warn">none</span>';

  const nodeBadges = document.getElementById('nodeBadges');
  const counts = result.node_counts || {};
  const entries = Object.entries(counts);
  nodeBadges.innerHTML = entries.length
    ? entries.map(([t, n]) => `<span class="badge">${t}:${n}</span>`).join('')
    : '<span class="badge warn">none</span>';

  if (data.tts_audio_b64) {
    document.getElementById('ttsAudio').src = 'data:audio/wav;base64,' + data.tts_audio_b64;
    document.getElementById('ttsAudio').play().catch(() => {});
  } else {
    document.getElementById('ttsAudio').removeAttribute('src');
  }
}

/* ============ text path (skips STT) ============ */
async function runText() {
  const text = document.getElementById('textIn').value.trim();
  if (!text) return;
  document.getElementById('micStatus').textContent = 'Running (text path)…';
  try {
    const form = new FormData();
    form.append('text', text);
    form.append('model', document.getElementById('model').value || 'gemma3:4b');
    form.append('tts', document.getElementById('tts').value);
    form.append('enable_tts', document.getElementById('enableTts').checked ? 'true' : 'false');
    const r = await fetch('/api/text', { method: 'POST', body: form });
    const data = await r.json();
    renderResult(data);
    document.getElementById('micStatus').textContent = 'Done (text)';
  } catch (e) {
    document.getElementById('micStatus').textContent = 'Error: ' + e.message;
  }
}

/* ============ voice recording ============ */
const recBtn = document.getElementById('recBtn');
const recLabel = document.getElementById('recLabel');
const micStatus = document.getElementById('micStatus');

let recording = false;
let audioCtx = null, source = null, processor = null, stream = null;
let chunks = [];

recBtn.addEventListener('click', async () => {
  if (!recording) await startRec(); else await stopRec();
});

async function startRec() {
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 } });
  } catch (e) {
    micStatus.textContent = 'Mic denied: ' + e.message;
    return;
  }
  audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: TARGET_SR });
  source = audioCtx.createMediaStreamSource(stream);
  processor = audioCtx.createScriptProcessor(4096, 1, 1);
  chunks = [];
  processor.onaudioprocess = e => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  source.connect(processor);
  processor.connect(audioCtx.destination);
  recording = true;
  recBtn.classList.add('recording');
  recLabel.textContent = 'Recording… tap to stop';
  micStatus.textContent = 'Recording…';
}

async function stopRec() {
  recording = false;
  recBtn.classList.remove('recording');
  recLabel.textContent = 'Tap to record';
  micStatus.textContent = 'Processing…';

  processor.disconnect(); source.disconnect();
  stream.getTracks().forEach(t => t.stop());
  await audioCtx.close();

  const total = chunks.reduce((n, c) => n + c.length, 0);
  const merged = new Float32Array(total);
  let off = 0;
  for (const c of chunks) { merged.set(c, off); off += c.length; }

  const wav = encodeWAV(merged, TARGET_SR);
  const form = new FormData();
  form.append('audio', new Blob([wav], { type: 'audio/wav' }), 'rec.wav');
  form.append('stt',  document.getElementById('stt').value);
  form.append('tts',  document.getElementById('tts').value);
  form.append('enable_tts', document.getElementById('enableTts').checked ? 'true' : 'false');
  form.append('model', document.getElementById('model').value || 'gemma3:4b');

  try {
    const resp = await fetch('/api/voice', { method: 'POST', body: form });
    const data = await resp.json();
    renderResult(data);
    micStatus.textContent = 'Done';
  } catch (e) {
    micStatus.textContent = 'Error: ' + e.message;
  }
}

function encodeWAV(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeStr = (off, s) => { for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); };
  writeStr(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  let off = 44;
  for (let i = 0; i < samples.length; i++, off += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
  }
  return buffer;
}
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- main
def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="EdgeSpeech workbench (LLM/BT pipeline)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7870,
                        help="default 7870 so it can run alongside the main app on 7860")
    args = parser.parse_args(argv)

    log.info("=== EdgeSpeech workbench %s:%s ===", args.host, args.port)
    try:
        import uvicorn
    except ImportError:
        log.critical("uvicorn not installed — run: pip install uvicorn[standard] fastapi")
        sys.exit(1)

    log.info("Workbench at http://%s:%s  (log: %s)", args.host, args.port, log_path())
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
