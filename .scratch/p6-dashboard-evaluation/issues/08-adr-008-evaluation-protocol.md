# 08: EV-001 — ADR-008: evaluation protocol (frozen before any 2019 run)

**What to build:** ADR-008 states, **before** any 2019 run with a real detector:
- the 2018/2019 partition and label isolation;
- the alert-to-leak matching rule: Tmax, earliest-alert matching, the duplicate policy, the false-alarm definition, and the treatment of the 4 leaks active on 2019-01-01;
- the metric names (detected, missed, detection delay, onset error, false alarms per week);
- region accuracy and BattLeDIM score as "not computed", with the conditions under which they would be;
- the manifest fields;
- the header and watermark rules.

Any BattLeDIM constant (Tmax, Xmax = 300 m along pipes, costs) is taken from the official rules and evaluation code, with the source cited, never from memory. P4 reviews it.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] ADR-008 present, indexed, with every rule above stated
- [ ] P4 review recorded (comment or approval) and status set to Accepted before EV-003 runs on 2019
- [ ] Sources for any BattLeDIM constant cited

Spec: `.scratch/p6-dashboard-evaluation/spec.md`
