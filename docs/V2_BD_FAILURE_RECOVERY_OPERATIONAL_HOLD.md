# V2-BD Failure/Recovery Gates — Operational Hold

Date: 2026-10-01

Status: **HOLD — ACTIVE**

Gate name:

`V2BD_FAILURE_RECOVERY_GATES_OPERATIONAL_HOLD_DUE_TO_ACTIVE_SIX_TWO_MACHINE_EXPERIMENT`

## Reason

The V2-BD positive two-machine routing pilot has already established correct routing on both physical execution machines. The next remaining qualification gates are deliberately disruptive: they require making machine 2's RemoteMCP node OFFLINE, restarting it, revoking a disposable identity, or injecting restart/crash/replay conditions.

A separate active SIX experiment currently requires continuous two-machine connectivity, including Wi-Fi-related experimental continuity. RemoteMCP qualification must not perturb that experiment.

This hold is operational only. It is **not** a PASS or FAIL result for the deferred V2-BD failure/recovery gates and it does not change the scientific/qualification question.

## Live state at hold entry

Both primary execution devices were observed ONLINE immediately before the hold was recorded:

- machine 1: `MACHINE_A_HOST`, `dev_<redacted-machine-a>`, route generation 1;
- machine 2: `MACHINE_B_HOST`, `dev_<redacted-machine-b>`, route generation 1.

At the hold snapshot:

- machine 1: 0 active commands, 0 active routed jobs;
- machine 2: 0 active commands, **2 active routed jobs**.

The presence of active routed work on machine 2 reinforces the requirement not to introduce an artificial node outage during the SIX experiment.

## Frozen operational prohibitions

Until this hold is explicitly released, RemoteMCP qualification MUST NOT:

- stop or kill the machine-2 `remotemcp.node` process;
- restart the machine-2 RemoteMCP node for qualification purposes;
- reboot or shut down machine 2;
- disable/restart its network adapter or intentionally interrupt its connectivity;
- revoke `dev_<redacted-machine-b>`;
- rotate or replace its device identity/key/runtime;
- alter route generation to provoke stale-generation behavior;
- open the node-2 OFFLINE/no-failover gate;
- open the same-identity reconnect gate;
- open any routed-job restart gate that requires interrupting node 2;
- inject CAS crash/restart behavior on node 2;
- run disposable revoke tests if they could perturb the two-machine experiment;
- repurpose the pilot node roots for real research repositories.

No workaround may simulate completion of those physical gates while this hold is active.

## Operations allowed during hold

Non-disruptive work remains allowed:

- `device_list` / `device_status` observation;
- documentation and README maintenance;
- ChatGPT plugin/package/user-guide work;
- static/unit/regression QA that does not touch the active physical node;
- source changes that are not deployed through a disruptive machine-2 restart;
- preparation of future failure/recovery gate locks/checklists;
- normal work already required by the active SIX two-machine experiment.

## Evidence already preserved before hold

The hold does not roll back previously established V2-BD evidence:

- two distinct physical execution devices paired under stable `dev_*` identities;
- simultaneous ONLINE operation;
- full 44-tool ChatGPT consumer surface;
- device-specific project registration;
- immutable task-to-device inheritance;
- machine-specific routed marker reads;
- isolated routed CAS writes/reads;
- routed durable jobs executed on the expected physical host and returned the expected `REMOTEMCP_DEVICE_ID`;
- no cross-route observed in the positive routing pilot;
- node reconnect after a real gateway restart demonstrated on machine 1 without replacing its identity.

## Deferred gates

The following physical failure/recovery qualification remains pending:

1. `NODE_2_OFFLINE_NO_FAILOVER_AND_SAME_IDENTITY_RECONNECT_GATE`;
2. machine-1-continuity while node 2 is offline;
3. durable routed-job observation/recovery across node-2 restart;
4. physical replay/conflict/stale-generation failure checks where still required by the frozen pilot contract;
5. physical CAS crash/restart recovery where still required;
6. disposable additional-identity revoke gate.

These gates remain pending, not failed.

## Hold release condition

This operational hold may be released only when the SIX two-machine experiment no longer requires uninterrupted connectivity, or the SIX operator explicitly confirms a maintenance window in which machine-2 RemoteMCP connectivity may be interrupted.

Before releasing the hold:

1. confirm both device identities and fingerprints are unchanged;
2. confirm no active SIX routed jobs/commands would be interrupted;
3. confirm both pilot roots remain isolated;
4. re-read the frozen V2-BD pilot gate order;
5. resume with the first deferred gate only — do not skip ahead.

## Next authorized RemoteMCP step

While the hold is active:

`V2BD_FAILURE_RECOVERY_GATES_OPERATIONAL_HOLD_DUE_TO_ACTIVE_SIX_TWO_MACHINE_EXPERIMENT`

After the hold is explicitly released:

`NODE_2_OFFLINE_NO_FAILOVER_AND_SAME_IDENTITY_RECONNECT_GATE`
