# 04: UI-004 — Alert detail with structured evidence

**What to build:** Selecting an alert opens its detail, which shows the structured evidence exactly as received, without recomputing anything:
- each deviating sensor with its deviation (m), z and since;
- the CUSUM statistic against its threshold;
- the estimated onset and `hours_since_onset`, as computed by the backend;
- the forecast contributions for the deviating sensors, when present;
- the detector summary;
- the forecaster and detector name and version.

The audit reference is shown raw (seq and hash), or "audit unavailable" when null; there is never a "verified" state. The narrative slot shows "explanation unavailable". `created_at` is shown only here, labelled "recorded by server".

**Blocked by:** 03

**Status:** ready-for-agent

- [ ] Headless tests: full alert, all-null alert and audit-null alert each render the expected markers
- [ ] No "verified" wording anywhere in the UI
- [ ] `just check` green

Spec: `.scratch/p6-dashboard-evaluation/spec.md`
