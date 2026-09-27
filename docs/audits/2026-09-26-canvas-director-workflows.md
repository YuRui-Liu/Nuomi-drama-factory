# Canvas director workflow evidence

Initial observation 2026-09-26 08:04:34 UTC; successful remote follow-up 08:18:06–07 UTC (16:18:06–07 Asia/Shanghai). The initial audit covered definitions and local contracts without paid operations. A later user-authorized paid acceptance run is recorded below. The machine-readable definition evidence remains `tests/fixtures/runninghub/canvas_director_contract.json`.

## Workflow identity and evidence boundaries

A read-only call to the live local server, `GET /api/v1/media-capabilities/providers/runninghub-main/workflows`, returned ordinary H3 `2089723723468328961`, H3 Ref `2096502793044582401`, and `video_minimax_h3_ref_max_images=5`. Both IDs match packaged profiles. This verifies configured identity only.

The initial attempt was blocked because workspace `.env` and workspace state had no provider credentials/settings; no remote request was sent then. This blocker is **superseded**: `scripts/start-local.sh` identified the actual data root `nuomi-drama-data`. Its configured account was read from an immutable SQLite snapshot after normal read-only access failed. The application's existing `CredentialResolver` and OS-backed credential store resolved its credential after approved escalation. Two authenticated reads then succeeded using `POST /api/openapi/getJsonApiFormat`, as specified by the [official documentation](https://www.runninghub.cn/runninghub-api-doc-cn/api-425749014). No keys or private media URLs were emitted or retained. Wigolo tools were unavailable, so the official page was read with the web fallback.

Remote SHA-256 hashes cover UTF-8 bytes of the exact response `data.prompt` string before parsing or sanitization:

| Workflow | Observed UTC | Remote definition SHA-256 |
| --- | --- | --- |
| Ordinary `2089723723468328961` | 08:18:06.985738 | `36cf43a116fd721d7b1abba85ef47c56aa3571e7980406ce29dc9e544c738d8f` |
| Ref `2096502793044582401` | 08:18:07.373545 | `ba96362a6c2b12445e3eb220ea7c73df73d23c533fb27c06237faaaa33564209` |

The contract's `sources` hashes separately identify exact local file bytes. Profile hash metadata is not substituted for remote hashes.

## Mode decisions

| Mode | Implementation target | Evidence | Current remote definition verified | Generation verified |
| --- | --- | --- | --- | --- |
| Pure Ref / r2v | Supported | Authenticated remote r2v definition and paid output | Yes | Yes |
| First frame / i2v | Supported | Ordinary profile and runtime | No | No |
| First + last / fl2v | Supported | Authenticated remote fl2v definition and paid output | Yes | Yes, one segment |
| Multiple segments | Supported | Remote Ref segments; local ordinary compiler | Ref only | Yes, Ref only |
| Ref + first frame | Disabled | Local compiler extension only | No | No |
| Ref + first + last | Disabled | Local compiler extension only | No | No |

`supported=true` means an implementation target based on the stated evidence. It is deliberately independent of `definition_verified` and generation `verified`. Unsupported hybrid combinations must fail explicitly; neither references nor frames may be silently removed. A failed prompt compilation must never submit a raw prompt.

The remote Ref graph confirms node 12 `MiniMaxH3Director`, `r2v — 参考主体生视频(Reference to Video)`, version 5 and `prompt_batch`. Its global references contain three images and its global generation image is empty. Both active segments have empty generation/end images and empty segment refs. Thus pure Ref must not require a first frame. There are two segments but three legacy shots and six keyframes: their presence is not evidence that mixed reference/frame conditioning is honored remotely. The existing reference compiler requires first frames and emits `Ref-I2V` / `Ref-FL2V`; its own docstring explicitly calls those local contracts pending provider verification. The independent canvas pure-Ref path must not inherit that requirement. Ordinary remote data instead contains one fl2v segment with both frame inputs, 124 frames at 24 fps; it does not independently verify i2v or ordinary multisegment operation.

Both authenticated remote definitions confirm `12.timeline_data` and the output chain: node 12 images/audio/fps → node 6 `CreateVideo` → node 7 `SaveVideo`.

## Dimensions and limits

The ordinary remote graph confirms node 12's `refine=["18",0]` connection and node 18 `MiniMaxH3DirectorRefine` saved at 896×1184, 3:4, one megapixel. The ordinary runtime overrides width, height, custom aspect ratio (`自定义`) and megapixels only for the packaged ordinary ID. The override's generation effect is not tested here. The remote Ref graph has neither node 18 nor a node 12 `refine` input; do not add that override to Ref or assume it exists for replacement IDs.

The live configured reference cap is five; the local compiler accepts a caller cap from one through ten. Ten is not a demonstrated provider maximum. The ordinary timeline uses 24 fps and rounds each segment to `17*k+5` frames. Remote Ref has totalFrames 180 and segment lengths 107 and 73, with saved durations four and three seconds. The saved graph is not a node input schema: those values establish neither provider frame/duration limits nor maximum reference/segment counts, which remain unknown.

## Local integration and browser observations

On 2026-09-26, the local Director integration suite exercised two complete fake-provider paths: ordinary first/last frames and pure Ref, each with two ordered segments. The real optimizer received frozen PNG bytes through a fake `StructuredTextRuntime`; the real compiler produced the submitted timeline. Assertions cover original prompt retention versus compiled prompts, one subject for two variants of the same character, image metadata and upload order, `17k+5` frame alignment, actual duration, workflow route, one submission, history after service restart, and a locally saved video checked by the real ffmpeg/ffprobe probe. The fake provider returned a small local video fixture; this is not provider output. No separate QC stage was invoked by either path. The backend command covering Director, asset library and relevant H3 modules completed with **288 passed, 8 dependency warnings**:

`PYTHONPATH=src /Users/liuyuxiang05/Liu/Nuomi-drama-factory/.venv/bin/python -m pytest tests/freezone/video_director tests/test_freezone_asset_library_backend.py tests/media_capabilities/video/test_h3_prompt_compiler.py tests/media_capabilities/video/test_h3_reference_payload.py tests/media_capabilities/video/test_h3_timeline.py -q`

The frontend real-component flow uses the `VideoDirectorNode`, its lazy panel, `AssetLibraryModal`, task hook, and real Director API serialization with MSW responses. It selects one base image and one costume variant from the same character, submits two ordered raw prompts, then observes a completed node video and history containing original and optimized prompts, route, workflow and actual parameters. Browser-relative API transport is adapted only in the jsdom test harness. The focused frontend run completed with **11 files and 53 tests passed**:

`./node_modules/.bin/vitest run src/__tests__/features/canvas/video-director-flow.test.tsx src/__tests__/features/canvas/video-director*.test.ts src/__tests__/features/canvas/video-director*.test.tsx src/__tests__/features/canvas/asset-library-character-images.test.tsx src/__tests__/features/canvas/node-registry.test.ts`

The parent-run frontend `npm run build` completed with exit 0: `tsc -b`, Vite build (9.57 seconds), and the bundle budget check passed. The freezone entry was 1,481,293 bytes against a 2,020,000-byte budget; the lazy `VideoDirectorPanel` chunk was 11,923 bytes. Vite emitted shared base UI/TanStack cycle and >500 kB chunk warnings, with no build error. This run was not compared against a clean baseline, so the warnings are recorded without assigning their origin.

A local browser preview of the real node, panel and asset modal was also observed at a localhost-only test page. With fake Director/asset responses and a synthetic two-second video, the flow retained both character images, two prompts, ordering and result after editor reopen and page refresh. It showed an explicit unsupported-hybrid message while preserving inputs, submitted generation without an approval step, kept polling after closing the panel, exposed playback and history, and remained scrollable at 640×800. These observations have no saved screenshot artifact and do not establish production app authentication or remote provider behavior. The full isolated app's authentication route hit a pre-existing Community Edition baseline limitation; the component harness avoided changes to unrelated auth code.

Both authenticated definition reads succeeded without paid work. Their sanitized extraction was held in `/private/tmp/canvas-director-live-definition-summary.json`; no raw graph or credential was persisted. The earlier update checked its hashes and timestamps against that extraction, parsed the contract, checked conservative mode flags, and scanned for secrets/private URLs. Paid acceptance subsequently began with explicit user authorization, as recorded below. Hybrid support stays false until evidence establishes that both reference and frame conditioning are consumed.

## Paid acceptance, 2026-09-26

The user explicitly authorized real verification and expense. The implementation worktree served the existing `shanhai_shiyi` project (`01M34T0S1BRCVENZAMM8RFA3YJ`) on localhost:8781. Supported production CLI attempt/retry endpoints submitted the tests; no direct ad-hoc provider generation script was used. No QC or human approval stage was introduced.

The initial attempt `373b34aa078f4219a952baf8117836ad` failed before any provider submission: the configured DeepSeek Harness adapter rejects image attachments (`DSH_IMAGES_UNSUPPORTED`). The existing, authenticated Codex route (`gpt-5.6-sol`, medium) was temporarily selected for the acceptance tasks and the exact original routing configuration restored after enqueue. A regression now verifies that this known failure gives an actionable message and never submits video; other arbitrary exception messages remain private.

2026-09-27 correction: the rejection was an adapter transport limitation, **not a limitation of `deepseek-v4-flash-vision-exp`**. DeepSeek's [official vision guide](https://api-docs.deepseek.com/guides/vision/) confirms the legacy alias accepts images and is served by the current Flash model. The former headless path supplied only a text task and rejected `images` before invoking DSH. The repaired runtime sends frozen image bytes as `BinaryContent` to the official DeepSeek image API using the configured model and `DEEPSEEK_API_KEY`; text-only tasks continue through headless DSH. Missing credentials stop before video submission with a fixed actionable error. The earlier Codex reroute describes the historical paid run, not a remaining requirement.

Real visual probe: the configured DeepSeek alias described the existing `修简铺` scene image as a rainy wooden interior with a table and a lantern on the left. A second real call passed the original frozen first/last images through the Director optimizer and returned one valid `fl2va` wire with 1,531 compiled prompt characters. This was an optimizer-only check; it did not upload images to RunningHub or submit another video. Related DeepSeek runtime, Director and routing tests passed (136 tests, eight existing dependency deprecation warnings). The original route setting remained unchanged.

### Ordinary H3 first/last: completed

- Director attempt: `39e609bb957247dd9bc9f6f82470d331`; application task: `4354d39f-08aa-4832-beb2-5d545354573c`.
- RunningHub task: `2103844083033407489`; workflow: `2089723723468328961`.
- Inputs: existing `修简铺` and `修简铺_夜晚` scene master images; one requested five-second segment, 16:9, 720p.
- Real runtime optimization completed before upload/submission. The attempt reached completed and saved `freezone/_outputs/video_director/39e609bb957247dd9bc9f6f82470d331.mp4` in the project output directory.
- `ffprobe`: H.264 High/yuv420p, 1280×736, 24 fps, 124 video frames, 5.166667 seconds of video; AAC LC audio, container duration 5.167 seconds, 381,564 bytes. Full `ffmpeg` decode to null completed with exit 0.
- First/middle/last frame inspection showed the same empty workshop composition and changing rain/window appearance. This is sample inspection, not a QC gate or a claim of perfect conditioning.
- Local media GET Range returned HTTP 206 with video/mp4. Direct navigation to the media URL in the in-app browser showed an unavailable player; the media response has a restrictive document CSP. Embedded production-panel playback has not been established by this direct navigation test.
- The project ledger records one successful RunningHub call (`25eee071-46ac-407e-b917-7e975e1d436c`), but no measured credit usage. Currency valuation is unpriced because the CNY exchange rate is missing. Codex calls are also unpriced. No exact monetary amount can be claimed.

### Pure Ref: completed after optimizer repair

Inputs are the base portrait and `E1雨夜常服` variant of the same `步知遥` character, with two five-second segments at 9:16/720p. Attempt `e95df70a5d2d4b71979086263c8edd83` and diagnostic retry `507b9714a6aa44ceaaecf15f2ea101cf` failed during optimization, with no RunningHub task created. The diagnostic retry identified `CODEX_STRUCTURED_OUTPUT_INVALID` after the runtime's bounded schema-repair calls. The original model response was ephemeral and unavailable; its exact invalid field cannot be asserted.

Local reproduction separately established that a malformed Ref retention value can cause the untagged union's Base errors to consume the entire 600-character runtime repair summary, hiding the real Ref error. Follow-up fixes separate mode-specific response schemas and explicitly state the already-enforced wire grammar. These findings do not justify claiming the earlier unknown response used that particular invalid value.

The post-fix attempt `aa1f18e735404d0ab4c9e6b632e36a2d` (application task `96148382-5490-4e69-9c9e-bb5621fc50f0`) completed both real runtime rewrites and submitted RunningHub task `2103849584924258306` to Ref workflow `2096502793044582401`. Both optimized wires declare one Subject with Picture 1 for identity and Picture 2 for the exact `E1雨夜常服` variant. Neither segment has a first/last frame.

The saved `freezone/_outputs/video_director/aa1f18e735404d0ab4c9e6b632e36a2d.mp4` passed complete ffmpeg decoding. ffprobe measured H.264/yuv420p, 736×1280, 24 fps, 248 frames, 10.333333 seconds of video and AAC audio; container duration 10.334 seconds, 2,271,776 bytes. Six sampled frames including both sides of the segment boundary show the same character/costume and sequential bamboo-scroll action without a reference-sheet collage. The boundary changes framing visibly; this test does not establish seamless continuous motion across independently generated segments. No extra generation was requested to hide this limitation.

Final verification: 89 Director tests and 135 shared H3 wire/optimizer tests passed (224 total); the Director run reported eight existing dependency deprecation warnings. Read-only code review approved mode-specific response schemas, unchanged content/identity guards, and safe private failure diagnostics. Unknown validation-location strings are redacted; diagnostics are excluded from public responses. Exact original runtime routing was restored after every temporary acceptance enqueue. This run submitted exactly two RunningHub video tasks and recorded ten Codex calls including schema repairs. No measured credit or currency amount was available in the ledger, so expense is unknown, not zero.
