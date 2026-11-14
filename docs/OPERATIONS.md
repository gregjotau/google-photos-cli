# Operating and recovering a migration

This client uses an undocumented Google Photos web protocol. The tested full-library migration was on one macOS account. Linux codec CI is useful coverage, not proof that Google sign-in and migration work on every Linux installation. Windows is unsupported. Login, passkeys, MFA, CAPTCHAs, operating-system restrictions and irreversible-action confirmations can still require the account owner.

## Choose a scope and a recovery policy

| Choice | Behavior |
| --- | --- |
| `--year 2023` | Capture year with the recorded UTC offset; keep it on every scoped command |
| `--batch-size 1` | One selected asset per verification/removal barrier; default combined cohort is 10 |
| `--limit 5` | Stable first five matches, including completed/kept items; rerunning does not advance to the next five |
| Default Live Photo policy | One AVIF still; original still and motion components remain locally backed up |
| `--live-photos keep` | Leave combined Live/Motion Photos unchanged in Google Photos |
| Default permanent intent | New verified sources wait in Trash until `--confirm-permanent-delete` is supplied |
| `--delete-mode trash` | Retain recoverable Google Trash; local backups remain mandatory |

Do not permanently remove cloud originals until you have checked that the backups are readable, complete and on a disk you intend to keep. File integrity is not visual quality: inspect the smoke-test images and videos yourself. The settings are intentionally lossy. Keep specialized/HDR/RAW assets outside the conversion scope if their original presentation matters. Avoid editing or reorganizing the selected media during a migration: metadata is based on its initial snapshot, not continuous synchronization.

Google says Trash does not count toward account storage. Permanent deletion removes recovery; it is not an extra quota-saving step. The CLI never empties unrelated Trash, upgrades old completed Trash-mode journals, or touches Locked Folder.

## A complete 2023 smoke test and migration

Install prerequisites and clone the repository as described in README. Use a disk with room for all originals, encoded outputs, temporary PNGs, and temporary video exports. A large video may temporarily occupy space as a downloaded ZIP, extracted source and local backup; streaming reduces RAM requirements, not disk requirements.

Terminal 1:

```sh
cd ~/google-photos-cli
export GOOGLE_PHOTOS_DATA_DIR="$HOME/google-photos-data"
uv run --locked google-photos-cli.py doctor
uv run --locked google-photos-cli.py self-test
uv run --locked google-photos-cli.py connect
```

Sign in to the intended account. In Photos web settings, record the previous upload preference and select **Original quality** before any uploads. Keep Chrome and terminal 1 running. The CLI does not change account settings itself.

Terminal 2:

```sh
cd ~/google-photos-cli
export GOOGLE_PHOTOS_DATA_DIR="$HOME/google-photos-data"
uv run --locked google-photos-cli.py plan --year 2023 --limit 5
uv run --locked google-photos-cli.py run --year 2023 --limit 5 --apply
# Exit 2 can mean media finished but permanent removal awaits confirmation.
uv run --locked google-photos-cli.py verify-backups --year 2023 --limit 5
uv run --locked google-photos-cli.py audit --year 2023 --limit 5 --verify-exports
```

The sample audit may also exit 2 while `pending_permanent_deletions` is nonempty. Check `media_complete`, the output files, cloud dates/albums and playback; investigate other errors rather than assuming every exit 2 is harmless. Review the sample's private `permanent-delete-plan.json`, then approve and finish that scope:

```sh
uv run --locked google-photos-cli.py purge-originals --year 2023 --limit 5
uv run --locked google-photos-cli.py purge-originals --year 2023 --limit 5 --apply --confirm-permanent-delete
```

Once the full year is authorized, continue with incremental permanent removal:

```sh
uv run --locked google-photos-cli.py run --year 2023 --batch-size 10 --apply --confirm-permanent-delete
uv run --locked google-photos-cli.py verify-backups --year 2023
uv run --locked google-photos-cli.py audit --year 2023 --verify-exports
uv run --locked google-photos-cli.py report
```

Each cohort must finish verification and source cleanup before the next cohort begins. Videos have an additional one-at-a-time barrier. `--batch-size 1` minimizes temporary cloud usage. A failed deletion or stalled verification stops admission of new cohorts. Already verified sources can remain in Trash if a cohort is interrupted; resume the same command or use the scoped `purge-originals` command to reconcile them.

Clients with per-batch action-time confirmation must use repeated small conversion-and-confirm cycles. For example, after the first five finish, `--limit 10` includes the same five completed sources plus the next five, then `--limit 15` includes the next five. Never drop the year filter or purge unrelated Trash. Do not postpone all source cleanup until the entire library has been uploaded.

`report` summarizes all confirmed replacements in this data directory, not a year-filtered subset. The filtered audit defines which selection has passed. It is not a claim about other years. Replace every `--year 2023` consistently if choosing another scope, or remove it everywhere for an authorized whole-library migration.

For a recoverable-Trash workflow, use `--delete-mode trash` on the `run` commands and omit `purge-originals`; still verify backups and exports. After the last upload and checks, restore the previous upload preference if desired, stop `connect` with Ctrl-C, and protect the data directory independently.

## Recovery by symptom

| Symptom | What to do |
| --- | --- |
| Missing native executable / `doctor` fails | Install the missing tool or codec; rerun `doctor`. No Google account is needed for diagnosis. Modern libavif with `avifenc -q` and AOM is required. |
| Missing `.bridge.json`, connection refused, bridge timeout | Start/restart `connect` with the same data directory/account. For an existing Chrome session, restart `broker`, generate a new `bootstrap`, and inject its current contents. Old session keys cannot reconnect to a new broker. |
| Chrome asks to connect to local device | The owner must allow the loopback connection in Chrome. Do not disable browser security or export cookies. |
| Chrome is blocked by Screen Time or authentication | Have the owner unlock it normally. A CLI cannot bypass that restriction. |
| Refresh/navigation during managed `connect` | The worker reattaches when the signed-in Photos page is ready. A manually injected worker must be re-injected after navigation. Timed-out writes still require reconciliation. |
| HTTP 429 / temporary server errors | Read-only RPCs have bounded retry/backoff. Writes stop on ambiguity. Wait, reconnect if necessary, and resume the same command. Never create an unbounded retry loop. |
| `committing` with no replacement ID | The commit may already have succeeded. Resume performs hash lookup only. If the hash is not visible, wait and retry later. Do not erase `stage`, change output bytes, or force another commit. A stale ambiguous commit needs investigation, not an automatic new upload. |
| Processing / export hash / playback pending | Keep the source and journals. Re-run later with the same filters. Waiting does not weaken date, size, hash or playback requirements. |
| Storage saver replacement | Set Original quality for future uploads. For videos, use `videos --year 2023 --repair --apply`, then audit and follow the removal flow. The current repair command is video-only; image quality mismatches require investigation while retaining the source. Never edit proof fields to force removal. |
| Local backup hash or size mismatch | Stop. Restore the original backup from an independent copy or investigate the export representation. Do not overwrite the recorded checksum to make it pass. |
| Wrong account | Disconnect, select the correct profile/data directory, and reconnect. Do not edit the journal account field. |
| Another workflow holds the data-directory lock | Finish or stop that process first. Use `status`/`report` for local inspection. Never run two modifying workflows against the same account. |
| Permanent deletion interrupted | Keep its `permanent_delete_started` flag and rerun `purge-originals` with the same filters/confirmation. It checks Trash and source identity before reconciling; do not change policy halfway through. |
| New arrivals make a full audit fail | Finish outstanding conversions/repairs, run `scan --append`, preview, then process with your intended filters. Do not overwrite the original inventory or abandon its journals. |

Exit codes: 0 means that command passed; 2 means a migration/audit remains incomplete or confirmation is pending. Other failures may exit 1. `doctor` exits 1 when prerequisites are missing. Read the result, not just the code. A preview succeeding does not mean migration is complete.

## Repeat runs and new arrivals

```sh
uv run --locked google-photos-cli.py scan --append
uv run --locked google-photos-cli.py plan --year 2023
uv run --locked google-photos-cli.py run --year 2023 --apply
```

Append is a read-only cloud inventory operation. It retains historical source IDs and their selection order, excludes journaled replacement IDs and superseded videos, and appends new active IDs. It refuses unfinished commits/conversions/repairs. Metadata for new IDs is fetched on the next `plan`/`run`. A regular `scan` is for initialization; once journals exist it refuses to replace them with a current-library baseline.

Keep journals for future runs. AVIF filenames can be skipped without a download, but a video without its completed journal may be evaluated/encoded again; filenames alone do not prove it is already optimized. When moving the data directory to another disk, journal paths are currently absolute: retain the same mount/path or reconcile paths carefully before continuing. There is no automatic relocation tool.

## Continuing past known item failures

For a large scoped combined run, opt in with `run --year YEAR --apply --continue-on-item-error` (add the existing permanent-deletion confirmation only after reviewing that policy). The default remains stop-on-error.

This mode records `item_deferred` events and an atomic, account-bound `deferred-items.json` ledger. It can defer an unavailable source before downloading it; a capture-date failure before source removal, with a complete verified backup and unchanged encoded output; an explicitly uncertain upload commit, preserving its committing journal and output for hash reconciliation; or a recognized network/server error before commit. It continues unaffected items and their normal incremental cleanup. No source is marked converted or deleted by deferral. Missing-source entries explicitly report that no backup was possible.

Account or identity mismatches, corrupt/incomplete backups, changed outputs, unexpected errors, pending verification timeouts and any Trash/permanent-deletion failure still stop admission of new work. Mutating requests are never blindly retried. Failed uploaded candidates can remain in the account for inspection and consume temporary space; this mode does not delete them automatically. Journals keep their original stages and errors. The ledger is historical diagnostics, not permission to skip verification or reset an ambiguous commit.

After unaffected work finishes, the command prints a deferred-item summary and exits 2. It does not run the completion path or claim a complete migration. A fresh audit still reports unresolved originals/replacements. A subsequent run uses the canonical journal recovery rules; it does not silently exclude ledger entries. Review exceptions and retain the original inventory and journals.

## Verification and performance

Scoped audits still scan every active-library page, but refresh metadata only for selected sources/replacements and unknown arrivals. Known unrelated originals and replacements need no metadata refresh. Unfiltered audits refresh every active item. Missing selected replacements and retained sources still fail the audit. A successful video resume clears its previous error only after reaching a verified success stage.

- Every new replacement gets a byte-identical true-original export check before source removal. Normal audits fetch current cloud metadata and check recorded proofs. `audit --verify-exports` additionally downloads current replacements and re-hashes their bytes; it uses more time, network traffic and temporary disk space.
- `verify-backups` is entirely local and hashes all selected journaled original components. A skipped AVIF with no downloaded original is not counted as a backed-up original. Differences between the recorded download size and Google's reported upload size are reported separately from checksum failures. Permanent removal can accept that difference only for a single-file backup with an explicitly recorded, identity-bound `pLFTfd` true-original export proof. Legacy journals are never silently granted this provenance. Live Photo component totals remain strict. A legacy backup with no recorded checksum is reported as unverifiable; do not create a checksum retroactively and call that historical proof.
- Recovered video outputs without an encode journal are fully decoded and compared with the retained original's duration/audio before upload. Recorded output hashes must still match. The cloud duration fallback always references the original source, never the output being verified.
- Local hashes and SHA-1 upload fingerprints are streamed. Video exports are extracted to generated temporary filenames, never trusted ZIP paths. Small image batch exports still use bounded batches in memory.
- Combined cohorts admit at most `--batch-size` selected assets at once, default 10. Standalone video processing verifies/removes one source before admitting the next. An asset-count bound is not a byte/quota guarantee: one replacement must fit, and Google quota updates may lag. Some older backups are quota-exempt, whereas new Original-quality uploads consume storage; smaller replacements can therefore increase billed usage. Check the account during the smoke test, and see [Google's policy](https://support.google.com/photos/answer/10100180?hl=en). Never delete an unverified source to free space.
- The worker runs at most six tasks concurrently. Native AOM/x265 encoders do the expensive computation; Python coordinates them. Increase image workers only if CPU, RAM and disk have headroom. Running more processes bypasses useful bounds and increases throttling risk.
- Catalog checkpointing may replay up to 999 metadata reads after a hard crash, but never marks missing metadata complete. This reduces large-library JSON rewrite work without changing mutation gates.
- Deadlines prevent queued tasks from starting after expiry and abort Google requests, including response-body reads, when their task deadline expires. Local broker requests have a separate 30-second bound around its 20-second long poll. Expiry releases a worker slot even if a fetch does not settle after cancellation. Read-only retry delays must fit within the task deadline and recheck expiry/account before another request. Aborting cannot undo a Google write already received by the server: reconcile its journal instead of repeating it. Automatic retries apply only to explicitly read-only RPCs; commit intent and Trash readback remain authoritative for recovery.

## Reporting a problem

Provide the public commit, platform, tool versions, command with account-specific filters redacted, error type, and affected journal **stage**. Do not attach your data directory, originals, Chrome profile, bootstrap, `.bridge.json`, account email, Google media IDs, signed URLs, or unredacted logs. Native codec tests use synthetic assets and can often reproduce a codec problem without exposing photos.
