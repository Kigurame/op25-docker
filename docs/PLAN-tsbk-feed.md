# Plan: TSBK (non-voice signaling) display toggle + live feed

Status: implemented and verified (in repo, uncommitted)

## Goal
Let users toggle op25 to verbosity 11 so all non-voice TSBK trunking signaling
(voice grants, patches, group affiliations, unit registrations, IDEN/band-plan
updates, RFSS/network status broadcasts, etc.) is decoded and shown in a
dedicated live feed in the Status tab. The toggle persists in cfg.json.

boatbod op25 `tk_p25.py` decodes and logs these at `debug >= 10`; `-v 11`
enables everything. op25 supports live level changes via the `set_debug` UDP
terminal command (already used by the UI), so no restart is required for the
toggle.

## Backend changes
1. `render_configs.py:149` — raise startup verbosity clamp 10 -> 11 so `-v 11`
   survives restarts.
2. `control-plane/app/op25_ctl.py` —
   - add `LEVELS[11] = "TSBK detail (all non-voice signaling)"`
   - `set_debug` clamp 0..11
3. `control-plane/app/main.py` —
   - `/api/op25/debug/{level}` validation range 0..11
   - `GET /api/op25/tsbk` -> `{enabled, level}` (enabled = cfg verbosity == 11)
   - `POST /api/op25/tsbk` (admin) `{enabled}`:
     - on: remember previous verbosity in module var `_tsbk_base`, write
       `verbosity: 11` to cfg.json, `_rerender()`, live `set_debug(11)`; no restart
     - off: restore `_tsbk_base` into cfg.json, rerender, live `set_debug(base)`
   - `GET /api/op25/tsbk/feed` -> last ~150 op25 log lines matching
     `tsbk(0x` / `mbt(0x` / `unhandled` read from `/var/log/op25/op25.log`
   - helper to tail+filter the op25 log file

## Frontend (control-plane/static/index.html)
- Add `<option value="11">11 · TSBK detail (all non-voice signaling)</option>`
  to the `dbgLevel` select.
- Add a `TSBK detail: off/on` toggle button in Status -> op25 diagnostics
  toolbar; POSTs the toggle, updates button + select state. If op25 is not
  running it still persists in cfg.json (applies next start) with a hint.
- New "TSBK activity" panel in the Status tab (between diagnostics and Logs):
  hint + compact `<pre id="tsbkView">`, auto-refresh every ~2s while the
  Status tab is visible, auto-scrolls to bottom.
- Update `CONFIG_GUIDE` verbosity field text to mention 11.

## Docs/config
- `conf/cfg.json` `_template_notes.verbosity`: add level 11.
- `README.md`: add 11 row to verbosity table (lines ~162-172) and
  `/api/op25/tsbk*` rows to the API table.

## Verification
- Smoke-test endpoints by running the control plane locally (uvicorn) against
  conf/ (terminal calls return ok:false without op25 running; persistence and
  feed logic still exercised).
- Validate JS (structural bracket check + python html parser; no node on host).
