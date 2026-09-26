# Canvas director workflow evidence

Initial observation 2026-09-26 08:04:34 UTC; successful remote follow-up 08:18:06–07 UTC (16:18:06–07 Asia/Shanghai). This audit records current remote definitions and local contracts, **not a claim of remote generation success**. No generation task, upload, or paid operation was performed. The machine-readable sanitized evidence is `tests/fixtures/runninghub/canvas_director_contract.json`.

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
| Pure Ref / r2v | Supported | Authenticated remote r2v definition | Yes | No |
| First frame / i2v | Supported | Ordinary profile and runtime | No | No |
| First + last / fl2v | Supported | Authenticated remote fl2v definition | Yes | No |
| Multiple segments | Supported | Remote Ref segments; local ordinary compiler | Ref only | No |
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

Both authenticated definition reads succeeded without paid work. Their sanitized extraction was held in `/private/tmp/canvas-director-live-definition-summary.json`; no raw graph or credential was persisted. The earlier update checked its hashes and timestamps against that extraction, parsed the contract, checked conservative mode flags, and scanned for secrets/private URLs. **Paid ordinary first/last and pure Ref multisegment generations have not run.** Those supported routes need user authorization for expense and materials before production CLI calls can verify provider output. Hybrid support stays false until evidence establishes that both reference and frame conditioning are consumed.
