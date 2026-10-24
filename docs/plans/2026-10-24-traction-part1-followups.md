# Traction Part 1: deferred follow-ups

Minor findings from the per-task and final reviews, left for later (none block the owner's device).

- Task 1: minor (deferred): ArmouryCommands sleep field unused
- Task 1: minor (deferred): shared C# messages say "Armoury Crate services Overdrive manages" (ArmouryServices.cs:70, ServiceHardware.cs:113, ArmouryCommands Restore)
- Task 2: minor (deferred): settings.load skips _recover_armoury if file vanishes mid-read (negligible race)
- Task 2: minor (deferred): no direct core tests for retry cap/_should_apply/_triggers override; no direct settings_file tests
- Task 3: minor (deferred): install-order drop of a differing old record is not logged (log armoury_recorded/running at warning)
- Task 3: minor (deferred): migrate_from_settings lock + save/read retries sleep synchronously at startup (up to ~2.6 s)
- Task 3: minor (deferred): keep_unreadable wording "Armoury Crate record settings ... were reset"
- Task 3: minor (deferred): "Armoury switched" reapply skipped when user_set False (untested/unstated)
- Task 3: minor (deferred): TDD red run skipped
- Task 3: minor (deferred): unreadable record warning logged twice
- Task 3: minor (deferred): old-record note shows "yet ()" before the first attempt; empty-message failure logged on every poll
- Task 3: minor (deferred): move succeeding inside a switch press skips on_load (P8 re-stop) for that load
- Task 4: minor (deferred): no test pins LOGGER_NAME/DEFER_MESSAGE overrides
- Task 4: minor (deferred): reapply_core docstring points at removed controls.apply_controller
- Task 4: minor (deferred): Protocol.cs SystemUnavailable still says "Touchscreen and graphics control is not available."; long comment line
- Task 4: minor (deferred): src/curve.ts keeps dead controller/touch helpers (Task 7 ports them)
- Task 4: minor (deferred): helper_client.py module-level RESTARTED/STOPPING still hold Overdrive text (dead in Traction); is_transport_failure placed mid-constants; reapply_core _is_transport_failure docstring stale
- Task 5: minor (deferred): check-shared does not compare SOURCE keys with shared-files.txt at the commit; sync never deletes stale copies
- Task 5: minor (deferred): tamper test covers only a Python file
- Task 5: minor (deferred): TractionCore.cs controller code has no header naming c090174 (same as Overdrive's Protocol.cs)
- Task 5: minor (deferred): plugin.json has both author and publisher; ported lower-case test name
- Task 6: minor (deferred): migration runs while old Overdrive installed (later Overdrive 0.1 changes not taken)
- Task 6: minor (deferred): every migrated install gets user_set True (brief-mandated)
- Task 6: minor (deferred): old-Overdrive unblock is one-way
- Task 7: minor (deferred): Armoury toggle stays enabled under `blocked` (backend refuses; ruled read-only) — candidate for final fix wave
- Task 7: minor (deferred): unknownText prop dead code; valueLabel export; double blank line; component logic untested
- Task 4: minor (deferred): after an unreadable-file reset the controller keys are not kept (carry-over lost in that case only)
- Task 6: minor (deferred): fail-open on transient overdrive.json read error; NOT_MOVED shown next to update note with old Overdrive; get_state re-reads overdrive.json each poll while record absent
- Task 8: minor (deferred): helper filter "$dst*" matches siblings / wildcard chars; make-zip writes final name directly; no missing-top-level-file test
- Task 8: minor (deferred): link scan uses SilentlyContinue (fail-open on access denied); $expected tautology / parent-folder links unchecked; OK logged before restart; static-only script tests
- Final: minor (deferred): permanent MOVE_FAILED if both overdrive.json and .bak are unreadable with Overdrive absent; long docstring line; first-match settings scan; old Overdrive installed after Traction load undetected until next load
