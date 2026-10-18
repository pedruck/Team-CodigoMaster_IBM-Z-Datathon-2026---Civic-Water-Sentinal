# Audit and explainability (P5)

Owner: P5 (@Leo3107). Status: **running on the real stack.** Since 2026-10-18 the shared API on
the VM runs `main` with no mocks (XGBoost, CUSUM v1, regions, pipe network) and with the audit
chain and the stored explanations (BE-013), and the whole of 2019 was replayed into it (see "On
the shared API"). In that run every explanation is the deterministic text: Granite was not
enabled there; it was run on its own on the VM (see "Granite on the LinuxONE itself"). Contract D
(contracts §4) is `Proposed` until the team confirms it. The real watsonx.ai service has never
been called.

| What | Where |
|---|---|
| Hash-chained log (the real `AuditSink`) | `src/sentinel/audit/chain.py`: `HashChainAuditSink`, `audit_path_for` |
| Command-line verifier | `src/sentinel/audit/verify.py`: `python -m sentinel.audit.verify` |
| Structured facts and deterministic text | `src/sentinel/narrative/facts.py` |
| Validation of model-written text | `src/sentinel/narrative/check.py` |
| Pluggable LLM and fallback | `src/sentinel/narrative/explain.py` |
| Granite on watsonx.ai | `src/sentinel/narrative/watsonx.py`: `WatsonxNarrator` |
| Granite on the LinuxONE itself (GGUF through llama.cpp) | `src/sentinel/narrative/local.py`: `LlamaCppNarrator` (in process), `LocalGraniteNarrator` (server) |
| Explanations generated in the background, stored and audited | `src/sentinel/narrative/store.py`: `ExplanationService`, `ExplanationStore` |
| Explain one alert from the command line | `src/sentinel/narrative/__main__.py`: `python -m sentinel.narrative [--llamacpp \| --local \| --watsonx]` |
| API with the chain and the explanations wired in | `SENTINEL_AUDIT_SINK=chain` in `sentinel.main` since BE-013 (README); `src/sentinel/audit/devserver.py` only keeps the earlier command working; the wrapper and `narrator_for` live in `src/sentinel/narrative/runtime.py` |
| Invented alert for tests and notebooks | `src/sentinel/narrative/mock.py` (`is_mock=True`) |
| Tests | `tests/test_audit_chain.py`, `tests/test_narrative.py`, `tests/test_local_granite.py`, `tests/test_watsonx.py`, `tests/test_explanation_store.py`; the backend wiring (P3) in `tests/test_audit_wiring.py` |
| Walk-through | `notebooks/p5_audit/p5_audit_narrative.ipynb` |
| Granite on the VM (discovery, load, explain) | `notebooks/p5_audit/p5_linuxone_granite.ipynb` |

Since BE-013 the backend selects the chain by configuration: with `SENTINEL_AUDIT_SINK=chain`
(plus `SENTINEL_AUDIT_RUN`, `SENTINEL_AUDIT_DIR` and `SENTINEL_NARRATOR`), `sentinel.main` opens
the chain, verifies it and wraps it with the explanation service. Tests and notebooks can still
inject a sink with `build_pipeline(audit_sink=...)`.

## Team decisions (2026-10-17/18)

| Topic | Decision |
|---|---|
| Narrative model | Granite: on the LinuxONE itself (`LlamaCppNarrator`) or on watsonx.ai (`WatsonxNarrator`) |
| Q-AUD-03, audit append fails | **Fail-closed** (team decision in `team-workflow`, implemented by P3 on `main`, ADR-006). An alert is served only after its `alert.raised` event is recorded. Failed events wait in an ordered queue and are retried before every new window; when the queue is full (`SENTINEL_AUDIT_PENDING_CAPACITY`), the answer is 503 `audit_backlog_full`. Window status: `recorded`, `pending` or `disabled`. |
| Q-AUD-02, where the log lives | `~/shared/civicwater/audit/<run>.jsonl` on the VM, outside every git clone (`audit_path_for(run)`) |
| Language | **English only**, like the rest of the project: every explanation, prompt and doc |
| Q-DET-02, sign of z | `deviation_m = observed − expected` (negative on a drop) and `z = (expected − observed)/σ` (positive on a drop). P5's invented alert and prompt examples follow it; the validation compares absolute values. |
| Alert 1.1 (ADR-011, P4) | New optional `candidate_pipe` (a pipe ID such as `p147`) and real L-Town regions (such as `Z3-R1`). P5 shows the pipe as "Candidate pipe (not a confirmed location)". The validation accepts that ID and rejects any other pipe. The small model's prompt leaves the pipe out, for the same reason as the contributions. |

An earlier version of this document recorded fail-open, which was what the backend did at the
time. `main` pointed out the contradiction (Q-DOC-01); this document now follows `main`.

## `[SIMULATION]`: gone on the real stack

An alert is a simulation when its forecaster **or** its detector is a mock (`is_mock=true`); the
code then adds `[SIMULATION]` to its explanation. State on 2026-10-18:

| Piece | State |
|---|---|
| Real forecaster (P2) | XGBoost `xgboost-33@0.1.0` in `~/shared/civicwater/models/` on the VM (`SENTINEL_FORECASTER=xgboost`), `is_mock=false`; TreeSHAP only for the sensors of an alert (`on_alert`). |
| CUSUM calibration (P4) | `config/calibration.xgboost-33.json` (v1, PR #21): `provenance = battledim-2018-oof`, so `is_mock=false`; sha256 `fad6ec0b…` (ADR-008 §G, pinned by `tests/detection/test_committed_calibration.py`); k 0.45, h 128, N 4, c 1.5, h_close 96. |
| Regions and pipe network (P4) | `config/regions.battledim.json` and `l-town.json` in the data directory. |
| Real 2019 data (P1) | The whole 2019 replay passes validation (`docs/runbook-vm.md`). |

So on the shared API the alerts come with `is_mock=false` and their explanations carry **no**
`[SIMULATION]`; P5 did not have to change anything. Checked on this laptop on 2026-10-18:
`sentinel.main` with the committed calibration, the chain and the explanations, and a stand-in
forecaster that reports the served identity (the XGBoost weights only exist on the VM), gives
`mocks_active: false`, alerts with `is_mock=false`, explanations without `[SIMULATION]` and
`verify_explanation` OK. That check covers the wiring, not the quality of the detection.
Anything run with a mock (tests, notebooks, the invented alert) still carries the notice, as it
must.

**Windows checkouts:** with `core.autocrlf=true` the calibration file is checked out with CRLF
line endings. Its sha256 then becomes `e55ca826…` instead of `fad6ec0b…`, the detector version
reads `0.1.0+e55ca826` and `test_committed_calibration.py` fails. The VM and CI (Linux) are not
affected. `.gitattributes` pins LF only for `config/regions.battledim.json`; the calibration file
needs the same line (P4).

## Contract D: audit record v1.0

One JSON line per event received from the pipeline (`AuditEvent`: `alert.raised`,
`window.scored`, `window.not_scored`):

| Field | Content |
|---|---|
| `record_version` | `"1.0"` |
| `seq` | 0, 1, 2… assigned by the sink; continues after a restart |
| `recorded_at` | server clock when written (UTC, `Z`) |
| `type`, `window_end`, `alert_id` | from the `AuditEvent` |
| `payload` | from the `AuditEvent` (for `alert.raised`: the alert **without** its `audit` field) |
| `payload_sha256` | `SHA-256(canonical_json(payload))` |
| `prev_hash` | `hash` of the previous record; the first record uses 64 zeros |
| `hash` | `SHA-256(prev_hash ‖ canonical_json(record without prev_hash and hash))` |

Canonical JSON is `json.dumps(v, sort_keys=True, separators=(",", ":"), allow_nan=False)` in
ASCII, the same rule as `SensorWindow.content_hash`. Floats use Python's `repr`, which is the
same on x86 and s390x (checked: identical `payload_sha256` on both). A verifier in another
language must reproduce that serialisation; if one is ever needed, move to RFC 8785 (JCS) in a
v2.

The pipeline gets back `AuditRef {seq, hash}`. The alert served by `GET /v1/alerts/{id}` carries
that pair in `audit`.

**Alert ↔ record link (Q-AUD-05):** the `payload_sha256` of the `alert.raised` record is the hash
of the alert without `audit`. Anyone holding the alert's JSON can check it with
`verify_alert(alert, record)` or `alert_payload_sha256(alert)`. This is tested with the JSON
returned by the API.

**Sink guarantees:**
- Append-only and thread-safe.
- Every line is flushed and fsynced before `append` returns.
- A failed write raises `AuditError` without using up a `seq` or leaving half a line behind.
- On start-up the existing file is verified, and a broken file is **never** extended (start-up
  fails with `AuditError`).

With fail-closed, the pipeline retries an event whose append failed, so `AuditError` must mean
"not stored". Before every write the sink therefore checks that the file still has the size it
left it with. If another process wrote to the file, or a failed write could not be undone, the
sink refuses with `AuditError` instead of writing a second record with the same `seq`. The
pipeline then holds the alerts and the problem becomes visible; restarting the process
re-verifies the file. Use **one file per process**.

**Reads:**
- `records()`, `records_after(after_seq, limit)` and `get(seq)` read only the bytes the sink has
  completely written, so a read concurrent with an append never sees half a line.
- `records_after` seeks to a per-`seq` byte offset kept by the sink, so a page costs O(limit),
  not a scan from the start of a year-long log (review of PR #17).

**Limitation:** whoever can rewrite the whole file can recompute a consistent chain. The defence
is the anchor: at the end of every replay, write `sink.head` (`seq` + `hash`) down outside the
VM and verify with `--anchor-seq/--anchor-hash`. An older anchor stays valid while the chain
grows, and a truncation below it is detected.

## Stored and audited explanations (generated in the background)

Granite takes ~30 s per explanation on the VM, so it cannot run when the operator opens an
alert, and the text the operator saw has to be on record.

**How it works:**
1. The audit chain records the `alert.raised` event, and the `ExplainingAuditSink`
   (`sentinel.narrative.runtime`, selected by `SENTINEL_AUDIT_SINK=chain` since BE-013) queues
   the alert on the `ExplanationService`. Queuing never fails the append:
   with fail-closed, a failure there would make the backend retry an event that is already in
   the log.
2. A single background worker calls `explain()` (Granite + validation + fallback), outside the
   window pipeline, whose order and latency do not change.
3. The result goes to `~/shared/civicwater/audit/<run>.explanations.jsonl`. It is a chain with
   the **same format** as the audit log (`type = "explanation.generated"`), so
   `python -m sentinel.audit.verify` checks it. The payload carries the whole `Explanation`
   (text, facts, validation, the model's raw text, the model used), plus `text_sha256`,
   `attempt` and `alert_payload_sha256`.
4. `verify_explanation(explanation_record, alert_record)` checks three things: the explanation
   belongs to that `alert.raised` record, it was made for the same version of the alert, and its
   text matches the recorded hash.

**Rules** (one explanation per alert version):
- When the model answers, it is called **once**. Accepted and rejected texts are both final:
  with `temperature=0` a new call would give the same text.
- When the model **fails** (error or empty answer), the deterministic text is stored and shown,
  and a later `submit` tries the model again, up to `max_attempts` (3) calls in total.
- Everything stays in the log, and the latest record of an alert is the current one. After a
  restart the service reads the log, returns the **latest** version of each alert (review of
  PR #17) and does not call the model again.
- If the explanations log cannot be written (for example, someone wrote to the file), the
  explanation stays `pending` with the deterministic text, and a broken log is never extended.

**Reading it** (Q-AUD-07, in `sentinel.api` since BE-013, contracts §5):
`GET /v1/alerts/{id}/explanation` returns an `ExplanationStatus`:
- `status`: `pending` (with the deterministic text meanwhile) or `ready`;
- `source`, `text`, `generator`, `generated_at` and `fallback_reason`;
- `alert_payload_sha256` and `record` (the `seq`/`hash` in the explanations log).

An unknown alert, or a server whose audit sink is not `chain`, gives 404 `not_found`. The chain
itself is read with `GET /v1/audit?after_seq&limit` and `GET /v1/audit/head` (Q-AUD-06).
Environment: `SENTINEL_NARRATOR` = `none` (default, deterministic text only) | `llamacpp` |
`local` | `watsonx`.

On the VM, a private API with Granite loaded in the process (1 thread):

```bash
SENTINEL_AUDIT_SINK=chain SENTINEL_AUDIT_RUN=leo-test SENTINEL_NARRATOR=llamacpp uvicorn sentinel.main:create_app --factory --host 127.0.0.1 --port $API_PORT
```

The earlier command, `uvicorn sentinel.audit.devserver:create_app --factory`, still works: it is
`sentinel.main` with `SENTINEL_AUDIT_SINK=chain` forced. The shared API is started only with
`scripts/shared_api.sh` (`docs/runbook-vm.md`, "Audit chain and explanations").

**Access:** use `--host 127.0.0.1`. The VM is shared and the API has no authentication, so with
`0.0.0.0` alerts and explanations would be open to anyone who can reach the port. To look from
outside the VM, use the SSH tunnel, as the dashboard does (ADR-009, `docs/runbook-vm.md`).

**Cost of fsync (review of PR #17):** every record is fsynced before `append` returns, because
the contract requires it to be durable before returning. The 2019 replay writes one event per
window (105 120 fsyncs). On a laptop it cost ~0.35 ms per record with fsync against ~0.11 ms
without. Measured on the shared API (P3, `docs/performance.md`): the chain on `~/shared` costs
**0.79 ms p50 per window**, about as much as the forecast (0.70 ms), and the whole of 2019 took
338 s with it.

**Pacing the demo (review of PR #17):** with one worker and 25–45 s per explanation, a fast
replay with hundreds of alerts builds hours of queue. That is not an error: while it waits, the
explanation shows as `pending` with the deterministic text. For the demo, there are three
options:
- replay slowly (`scripts/replay.py --speed`);
- show alerts whose explanations were generated beforehand;
- use `SENTINEL_NARRATOR=none` (deterministic text, instant) and show Granite on its own in the
  notebook.

With the 895 alerts of 2019 (below), Granite at 25–34 s each would need about 6–8 h of
background work for the year.

### On the shared API (2019, 2026-10-18)

Run by P3 (PR #23; `docs/performance.md`, `docs/runbook-vm.md`): `main` @ `45f733d` started by
`scripts/shared_api.sh` with XGBoost, CUSUM v1, regions, pipe network and
`SENTINEL_AUDIT_SINK=chain SENTINEL_AUDIT_RUN=replay-2019-1018`, no narrator (deterministic
text). The whole of 2019 was replayed into it with `scripts/replay.py`.

| Measure | Value |
|---|---|
| Windows | 105 120 × `201` in 338 s: 144 `insufficient_history`, 104 976 `scored`; audit backlog 0 |
| Alerts | 895, all `is_mock=false` |
| Audit chain `replay-2019-1018.jsonl` | 106 015 records (105 120 windows + 895 alerts), `verify_file` ok; `verify_alert` of an alert against its record ok |
| Explanations `replay-2019-1018.explanations.jsonl` | 895 records, `source: template`, `verify_file` ok |
| Chain fsync on `~/shared` | 0.79 ms p50 per window |
| Anchor at the end of the run | audit seq 106 014, hash `96fa38333d56681f5de7aa09e0ce46d42977e712869b7ed951cb02c6a15b0856`; explanations seq 894, hash `2c93bfe6c39709e3faae9b704607b1dd917ea02079c3efbb5ee393edbed97b03` |

To check it later (from the integration copy on the VM):

```bash
cd ~/shared/civicwater/main && PYTHONPATH=src python -m sentinel.audit.verify ~/shared/civicwater/audit/replay-2019-1018.jsonl --anchor-seq 106014 --anchor-hash 96fa38333d56681f5de7aa09e0ce46d42977e712869b7ed951cb02c6a15b0856
```

The anchor is also recorded in git (`docs/performance.md`), outside the VM, which is what makes
a rewrite of the whole file detectable.

**Alert volume.** 895 alerts is about 17 per week; P3 counted up to 28 alerts for one region and
onset, and 37 % of consecutive alerts within 30 min of each other. Whether they are leaks is the
2019 evaluation's job (EV-003); the repeated alerts of one episode are Q-DET-09 (P4). For P5 it
means one explanation per alert, and many explanations of the same episode.

## Explaining an alert

1. `alert_facts(alert)`: JSON with the alert's data only (region, candidate pipe, sensors,
   deviations, z, onset, CUSUM, the top 3 contributions per sensor, models, `audit_seq`). It is
   the structured explanation and the model's only input.
2. `render_text(alert)`: deterministic English text; always available.
3. `explain(alert, narrator)`: calls the model. The text is accepted only if `check_narrative`
   finds nothing outside the payload; otherwise, or if the model fails, times out or answers
   nothing, the deterministic text comes back with a `fallback_reason`. When the alert is a mock,
   the code adds `[SIMULATION]` itself; the model is not trusted to.

`check_narrative` rejects:
- any sensor, region, pipe or other ID that is not in the alert;
- any number that does not match a value of the alert (rounded to the decimals written, by
  absolute value);
- any date or time that is not `window_end`, `suspected_onset` or a `since`;
- phrases that confirm or rule out a leak ("confirmed leak", "there is a leak", "definitely",
  …);
- a mock alert's text without `[SIMULATION]`.

The text is English: the decimal separator is ".", and a comma inside a number ("0,91") is
read as two numbers and rejected.

It also rejects **numbers in the wrong place** (`misattributed_numbers`). In each clause (split
at ". ", "; ", ": " and line breaks):
- a deviation or z-score must belong to a sensor named in that clause (or, when the clause names
  no sensor, to any deviating sensor);
- a forecaster contribution needs its feature name in the same clause;
- a number followed by "m" (metres) must be a deviation or a contribution, never a count, an
  hour, a CUSUM value or a z-score ("P14 read 3 m below" with 3 deviating sensors is rejected);
- other global values (CUSUM, threshold, hours since onset, counts, audit seq) can appear in any
  clause;
- days and years are **never** bare numbers. Dates are only accepted as dates (`2019-03-14`,
  `14/03/2019`, "March 14, 2019", "14th of March", "March 2019") and must be dates of the alert.
  Before the review of PR #17, "P14 read 14 m" passed because the alert is on the 14th.

The clause rule comes from a real case on the VM: Granite 350M wrote "P14 and P21 read 0.21 m
and 0.17 m below", using the contributions of `hour_of_week` as deviations. Every number existed
in the payload, so the earlier rule accepted it.

Remaining limits:
- it cannot see two values swapped between sensors named together ("P14 and P21 read 0.62 m and
  0.91 m");
- it does not check numbers written in words ("three");
- the only unit it recognises is "m".

`Explanation` carries `alert_payload_sha256` and `audit`, so the explanation is tied to the
audited version of the alert. The model never decides whether an alert exists: that is the
detector's job (P4).

## Granite on watsonx.ai

`WatsonxNarrator` uses the standard library only (nothing to install on the LinuxONE). It makes
two calls:
1. IAM token: `POST https://iam.cloud.ibm.com/identity/token` (grant
   `urn:ibm:params:oauth:grant-type:apikey`), reused until 60 s before it expires;
2. chat: `POST {WATSONX_URL}/ml/v1/text/chat?version=2024-10-08` with `model_id`, `project_id`,
   `messages`, `max_tokens=300` and `temperature=0`; the text is in
   `choices[0].message.content`.

| Variable | Content |
|---|---|
| `WATSONX_APIKEY` | IBM Cloud API key. **Secret:** never in git, in a notebook or in logs |
| `WATSONX_URL` | regional endpoint, e.g. `https://us-south.ml.cloud.ibm.com` |
| `WATSONX_PROJECT_ID` | watsonx.ai project |
| `WATSONX_MODEL_ID` | Granite chat model; on 2026-10-17 only `ibm/granite-4-h-small`, only in us-south and eu-de (see below) |
| `WATSONX_API_VERSION` | optional, default `2024-10-08` |
| `WATSONX_TIMEOUT_S` | optional, default 20 |

Usage: `explain(alert, WatsonxNarrator.from_env())`. Errors (HTTP, network, timeout, unexpected
answer) become `WatsonxError`, and `explain` falls back to the deterministic text with the
reason. The API key never appears in `repr`, error messages or `fallback_reason`. Still pending:
the real call was never made (no credentials here), and nobody has confirmed that the VM can
reach `*.cloud.ibm.com`.

Chat models per region (public endpoint, no token; repeat before the demo):

```bash
curl -s "https://us-south.ml.cloud.ibm.com/ml/v1/foundation_model_specs?version=2024-10-10&filters=function_text_chat&limit=200" | python -m json.tool | grep model_id
```

Result on 2026-10-17: `ibm/granite-4-h-small` in us-south (Dallas) and eu-de (Frankfurt), and no
Granite chat model in eu-gb, jp-tok, au-syd or ca-tor. The watsonx.ai project must be in one of
those two regions and have a watsonx.ai Runtime (Watson Machine Learning) service associated.

## Granite on the LinuxONE itself (no cloud)

What the team VM has (section 1 of `p5_linuxone_granite.ipynb`, 2026-10-17):

| Item | Result |
|---|---|
| Machine | s390x, 3 CPUs, 8.2 GB RAM (3.2 GB free at the time), NNPA (Telum) visible |
| Model | `~/granite-4.0-h-350m-base-be.f16.gguf`, 685 MB, GGUF v3, big-endian |
| Runtime | `llama_cpp` (llama-cpp-python) **installed in the Jupyter kernel**; no `llama-server`; `gcc` and `cmake` present |
| IBM's example | `~/Loan_Approval_Demo.ipynb` mentions Granite |

The model is Granite 4.0 H 350M **base**: small and **not** instruction-tuned. The prompt is
therefore few-shot: two invented examples with the same shape as the alert (with or without a
region), then the alert's facts (`prompt_for`). If the model copies an example or invents
anything, `check_narrative` rejects it and the deterministic text is used.

**Main path: `LlamaCppNarrator`.** It loads the GGUF once inside the Python process with
`llama_cpp`, serialises the calls, and frees the memory on `close()`.

| Variable | Default |
|---|---|
| `LOCAL_LLM_MODEL_PATH` | `~/granite-4.0-h-350m-base-be.f16.gguf` |
| `LOCAL_LLM_THREADS` | 1 (3 vCPUs for the whole team) |
| `LOCAL_LLM_CTX` | 2048 tokens |

To test it, run `python -m sentinel.narrative --llamacpp`. Exit code 0 means the text was
accepted, and the output shows `model_load_s`, `elapsed_s` and the token usage. The notebook
`p5_linuxone_granite.ipynb` loads the model, explains an invented alert and a pipeline alert,
and frees the memory.

**Alternative with a server: `LocalGraniteNarrator`.** It calls
`POST {LOCAL_LLM_URL}/v1/completions` (`LOCAL_LLM_URL`, `LOCAL_LLM_MODEL`,
`LOCAL_LLM_TIMEOUT_S`) on a `llama-server` or `python -m llama_cpp.server`. Use it if the model
must live outside the API process. The VM has no `llama-server` today.

### Results on the VM (`p5_linuxone_granite.ipynb`, 1 thread)

Runtime: `/opt/venv`, Python 3.12.15, llama-cpp-python 0.3.36. `system info`:
`zDNN : NNPA = 1`, `CPU : LLAMAFILE = 1, OPENMP = 1, REPACK = 1`. The build has zDNN support and
detected the NNPA; how much of the inference runs on the accelerator was not measured. The model
loads in 11.1 s the first time and in 0.5 s once the file is cached.

| Run | What changed | Outcome |
|---|---|---|
| 1 | first version | 34–44 s each. One text accepted but **wrong**: contributions passed off as deviations. One rejected (copied region `R2` from an example). One accepted but trivial. |
| 2 | smaller prompt (no contributions, detector summary, models or seq; 2-decimal numbers; `max_tokens` 120) and the numbers-in-the-wrong-place rule | 26.9–34.1 s; all three rejected (no `[SIMULATION]`, copied `R2`); no misattributed numbers |
| 3 | `[SIMULATION]` added by the code; examples with the same shape as the alert; the raw text kept | **3/3 accepted and correct**, 26.0–33.6 s; tokens (prompt + answer) 677+87, 548+68, 586+79 |
| 4 | after merging `main` (positive z in the prompt) | 3/3 accepted and correct, 25.3–33.4 s; **text and tokens identical to run 3**: with `temperature=0` the result is reproducible on s390x |

Run 3 had two Portuguese-language explanations, which the project has since dropped (English
only); the English one was checked by hand: values, sensor order, region, times and CUSUM all
match the alert. One imprecision the validation does not catch: the invented alert's text says
all three sensors are low "since 02:10", but P09 has no `since` (02:10 is the alert's estimated
onset).

**Where the time goes** (a rough estimate from three measurements): ~0.026 s per prompt token
(~38 tokens/s) and ~0.17 s per generated token (~6 tokens/s). That is ~15–17 s reading the
prompt and ~12–15 s writing, with 1 thread.

Ways to reduce it:
- fewer prompt tokens (e.g. one example instead of two; it changes quality, so it must be
  measured);
- 2 threads when the VM is idle;
- reuse the prompt prefix across alerts of the same shape (not verified for the hybrid model).

Even at ~30 s, the explanation fits easily within the 300 s between windows, as long as it is
generated in the background and stored.

**Conclusion and recommendation:**
- The infrastructure works on the LinuxONE: the model loads and generates, the validation rejects
  what is wrong, and the fallback takes over.
- The quality of the 350M *base* model depends on the prompt and the validation. The current
  version went 3/3 on a small, mock sample. The deterministic text remains the guaranteed
  fallback, and Granite's text is shown only when the validation accepts it.
- For better text: a Granite *instruct* model as a big-endian GGUF (e.g. a 350M or 1B instruct
  converted with `convert_hf_to_gguf.py --bigendian`), or `ibm/granite-4-h-small` on watsonx.ai.
- At ~30 s per explanation, generation must happen in the background, right after the alert, and
  be stored (done: `sentinel.narrative.store`, wired into the backend by BE-013).
- Granite has not run on the shared API yet: the 2019 run used the deterministic text for its
  895 alerts (see "On the shared API").

## Testing on a laptop (Windows, PowerShell)

From the root of the clone. `uv` was not on the PATH on this machine; `python -m uv` avoids
depending on it.

```powershell
python -m pip install uv
python -m uv sync --locked --group dashboard
python -m uv run pytest
```

CI gates: `python -m uv run ruff format --check src tests scripts dashboard`,
`python -m uv run ruff check src tests scripts dashboard` and `python -m uv run mypy`.

Notebooks (on Windows use `python -m jupyter`; `uv run --with jupyter jupyter lab` fails with
"os error 3" on this path):

```powershell
python -m uv run --with jupyter python -m jupyter lab
```

End to end through the API, in two terminals (`data/` is ignored by git):

```powershell
$env:SENTINEL_AUDIT_SINK="chain"; $env:SENTINEL_AUDIT_RUN="local-test"; $env:SENTINEL_AUDIT_DIR="data/audit"; python -m uv run uvicorn sentinel.main:create_app --factory --port 8000
```

```powershell
python -m uv run python scripts/make_synthetic.py data/synthetic.jsonl --count 288 --leak-at 200
python -m uv run python scripts/replay.py data/synthetic.jsonl --url http://127.0.0.1:8000
curl.exe -s http://127.0.0.1:8000/v1/alerts
curl.exe -s http://127.0.0.1:8000/v1/alerts/<alert_id> -o data/alert.json
curl.exe -s http://127.0.0.1:8000/v1/alerts/<alert_id>/explanation
python -m uv run python -m sentinel.audit.verify data/audit/local-test.jsonl --alert data/alert.json
python -m uv run python -m sentinel.audit.verify data/audit/local-test.explanations.jsonl
```

Expected (the default mocks): 288 windows accepted, 1 alert, an audit chain of 289 records with
`ok: true`, the alert `ok: true`, and the explanation `ready` with `source: template` and
`[SIMULATION]` (the laptop has no XGBoost weights, so the mocks stay on).
Restarting the server continues the same chains; delete the files in `data/audit/` to start
over.

Granite on watsonx.ai (needs credentials; the key is typed without being shown or kept in the
history):

```powershell
$env:WATSONX_APIKEY = [Net.NetworkCredential]::new('', (Read-Host 'API key' -AsSecureString)).Password
$env:WATSONX_URL="https://us-south.ml.cloud.ibm.com"; $env:WATSONX_PROJECT_ID="<project id>"; $env:WATSONX_MODEL_ID="ibm/granite-4-h-small"
python -m uv run python -m sentinel.narrative --watsonx
```

Exit code: 0 = Granite's text accepted; 1 = Granite was called but its text was rejected or it
failed (see `fallback_reason` and `check`); 2 = missing configuration.

## Running on the LinuxONE VM

1. **Get the code** into your clone (`~/shared/civicwater/users/<name>/repo`) and run
   `source ~/shared/civicwater/users/<name>/start.sh` in every new terminal. Do not
   `pip install -e` in a personal clone: the notebooks and commands use `PYTHONPATH=src` with the
   Python of `/opt/venv`.
2. **Tests:**
   `python -m pytest -q tests/test_audit_chain.py tests/test_narrative.py tests/test_local_granite.py tests/test_watsonx.py tests/test_explanation_store.py tests/test_audit_wiring.py`.
3. **Notebooks:** run `p5_audit_narrative.ipynb` and `p5_linuxone_granite.ipynb` with
   *Kernel → Restart Kernel and Run All Cells*. Before pulling again, discard what Jupyter saved:
   `git fetch origin && git switch --discard-changes -C <branch> origin/<branch>`.
4. **Private API with the chain and Granite** (one worker, `127.0.0.1`): the command in "Stored
   and audited explanations" above. Replay with
   `scripts/replay.py --url http://127.0.0.1:$API_PORT` (always pass `--url`: its default,
   port 8000, is the shared API), then read `GET /v1/alerts/{id}/explanation` until it is
   `ready`. The shared API on port 8000 already runs the real stack with the chain (see "On the
   shared API"); only `scripts/shared_api.sh` restarts it, after telling the team.
5. **After the replay:** run `python -m sentinel.audit.verify` on both logs and keep the `head`
   (`seq` + `hash`) **outside the VM** (the PR, the team chat, your laptop), as P3 did for the
   2019 run in `docs/performance.md`. In the demo, verify with `--anchor-seq/--anchor-hash`.
6. **watsonx.ai, if used:** check the network with
   `curl -s -o /dev/null -w "%{http_code}\n" https://iam.cloud.ibm.com/identity/token`. Any HTTP
   code (400) means it is reachable; a timeout or DNS error means egress is blocked. The VM has a
   single Linux user, so any file or `.bashrc` can be read by everyone: set the key only for the
   session (`read -rs WATSONX_APIKEY && export WATSONX_APIKEY`, or `getpass` in a notebook).
   Prefer a Service ID key with access to the project only, and revoke it after the event.

## Open questions

| Question | P5's position |
|---|---|
| Q-AUD-01, port and event types | Accepted as they are. |
| Q-AUD-05, circular reference | Solved as in ADR-006: the alert is hashed without `audit`. |
| Q-AUD-06, reading the chain over HTTP (replaces Q-AUD-04) | **Done (BE-013, P3).** `GET /v1/audit?after_seq&limit` returns records in `seq` order, exactly as stored, through `HashChainAuditSink.records_after` (O(limit) per page); `GET /v1/audit/head` returns `{seq, hash}`; read only. The backend returns **no** "verified" boolean: verifying is the client's job (`python -m sentinel.audit.verify`, `verify_records`). The rules are in Contract D above (contracts §4). |
| Q-AUD-07, explanation over HTTP | **Done (BE-013, P3).** `GET /v1/alerts/{id}/explanation` in `sentinel.api` (contracts §5), with `status` `pending`/`ready`, `text`, `generator` (the narrator's name), `alert_payload_sha256`, `generated_at` and `record`; 404 for an unknown alert or when the sink is not `chain`. The wrapper that queues the explanation moved to `sentinel.narrative.runtime` unchanged. |
| Q-DOC-01, `audit` vocabulary and fail-open in P5's doc | Solved on P5's side: the branch merged `main` (a merge, not a rebase, because the branch is published and `AGENTS.md` forbids `push --force`). `AuditStatus` is now `main`'s (`recorded`, `pending`, `disabled`), and this document records fail-closed. |

## What depends on other owners

- **P3 (backend): done.** BE-013 (PR #22, 2026-10-18): `SENTINEL_AUDIT_SINK=chain` with
  `SENTINEL_AUDIT_RUN` / `SENTINEL_AUDIT_DIR` / `SENTINEL_NARRATOR`, `GET /v1/audit`,
  `/v1/audit/head` and `/v1/alerts/{id}/explanation` (contracts §4–5), `WATSONX_*` and
  `LOCAL_LLM_*` in `.env.example`. The shared API runs the real stack with the chain (PR #23).
- **P4 (detection):** the final calibration is done (`config/calibration.xgboost-33.json`,
  PR #21), so alerts are real. Pending:
  - `.gitattributes` with `eol=lf` for the calibration file, as for `regions.battledim.json`,
    so that its hash is the same on Windows checkouts;
  - the repeated alerts of one episode (Q-DET-09), each of which gets its own explanation.

  The explanation already uses `region`, `candidate_pipe`, `suspected_onset`, `cusum` and
  `since`.
- **P1 (data):** the official BattLeDIM IDs (`config/sensors.battledim.json`: `n1`, `n54`,
  `n105`…) are on `main`, and the validation recognises them (letters followed by digits).
- **P2 (ML): done.** `contributions` (TreeSHAP) only for the sensors of an alert
  (`SENTINEL_FORECASTER_CONTRIBUTIONS=on_alert`), which the explanation lists.
- **P6 (dashboard): pending.**
  - Partly done in BE-027: the evidence pane, the detail and the Jupyter view show
    `explanation.text` and a note from `status`/`source`/`generator`/`fallback_reason`.
    Still missing: the explanation's `record` (`seq`) and the verification result.
  - Two Windows-only failures remain in `tests/test_eval_run.py` (`write_text` without
    `encoding="utf-8"`).

## Verification

Windows x86_64, Python 3.13.5, dependencies from `uv.lock` (`uv sync --locked --group
dashboard`, as in CI), unless stated otherwise.

| What | Result |
|---|---|
| Shared API on the real stack, full 2019 (P3, PR #23, VM, 2026-10-18) | 105 120 × `201` in 338 s; 895 alerts with `is_mock=false`; audit chain 106 015 records and 895 explanations (`template`), `verify_file` ok on both, `verify_alert` ok; anchors in "On the shared API" |
| `main` @ `d22c8a7` after BE-013, this laptop (2026-10-18) | `ruff format --check`, `ruff check`, `mypy` (179 files) clean; P5 tests plus `tests/test_audit_wiring.py` **150 passed** (the 2 failures of `test_committed_calibration.py` are the CRLF hash above); whole suite 1034 passed, 40 skipped, 33 failed, all Windows-only (CRLF of the calibration and regions files, the fixture and symlink of P4's offline tests, encoding in P6's evaluation tests). CI on `main` (Linux): green, 1067 passed, 40 skipped |
| `sentinel.main` end to end with the committed calibration, the chain and the explanations (stand-in forecaster with the served identity), 300 windows | 300 × `201`; `mocks_active: false`; 5 alerts with `is_mock=false`; explanations `ready` without `[SIMULATION]`, `verify_explanation` OK; `/v1/audit` and `/v1/audit/head` answer; both chains ok |
| English-only explanations, review of PR #17 addressed | `ruff format --check`, `ruff check`, `mypy` (152 files) clean; P5 tests **131 passed**; whole suite **855 passed**, 34 skipped, the same 4 Windows-only failures outside P5 as below (fewer tests than below because the Portuguese cases were removed); both notebooks executed in a real kernel (12/12 and 6/6 code cells, 0 errors; the VM notebook against a fake `llama_cpp`) |
| Review of PR #17 (P1): bare days/years, metres, latest version after a restart, safe and indexed chain reads | whole suite **863 passed**, 34 skipped, 4 failed: Windows-only and outside P5 (3 in `test_eval_run.py`, P6; 1 in `test_topology_files.py`, P4, whose regions file gets CRLF line endings on a Windows checkout, 428 bytes instead of 415). The reviewer ran the suite on Linux: 853 passed, 34 skipped. |
| Second merge of `main` (alert 1.1, L-Town regions) | no conflict; P5 tests pass with the backend's official example `alert.cusum-synthetic.json` |
| Real `uvicorn` end to end (`devserver`, before BE-013; `SENTINEL_NARRATOR=none`, official BattLeDIM registry, mocks), 288 windows | 288 × 201; 1 alert; `GET .../explanation` `ready`; audit chain ok (289 records) and explanations chain ok |
| LinuxONE VM (s390x): P5 tests on branch `leo/local-version` @ `033959d` | 93 passed (before the merges; not rerun since) |
| LinuxONE VM: walk-through notebook in the VM's Jupyter | every section ok: 289-record chain valid, tampering detected, alert `A-20190314T1640Z-1` (same ID and deviations as on x86) linked to record seq 200 |
| x86 vs s390x parity of canonical JSON | `payload_sha256` of `{"i": 1, "status": "scored"}` = `993ee9cc…20d79f` on both |
| LinuxONE VM: `p5_linuxone_granite.ipynb` | see "Results on the VM" above (4 runs) |
| LinuxONE VM: `devserver` (before BE-013, mocks) with `SENTINEL_NARRATOR=llamacpp` | `verify_explanation` of the stored explanation against its `alert.raised` record: OK |
| `foundation_model_specs` (public) per region | Granite chat only in us-south and eu-de (`ibm/granite-4-h-small`) |
| Granite acceptance rate over many varied alerts; Granite on real alerts (the 2019 run used the deterministic text) | **NOT_RUN** |
| P5 tests on s390x after the merges; watsonx.ai with credentials | **NOT_RUN** |
