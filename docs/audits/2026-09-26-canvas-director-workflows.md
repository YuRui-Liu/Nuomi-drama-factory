# Canvas director workflow evidence

Observed 2026-09-26 08:04:34 UTC (16:04:34 Asia/Shanghai). This audit permits implementation against local contracts, **not a claim of remote generation success**. No generation task, upload, or paid operation was performed. The machine-readable sanitized evidence is `tests/fixtures/runninghub/canvas_director_contract.json`.

## Workflow identity and evidence boundaries

A read-only call to the live local server, `GET /api/v1/media-capabilities/providers/runninghub-main/workflows`, returned ordinary H3 `2089723723468328961`, H3 Ref `2096502793044582401`, and `video_minimax_h3_ref_max_images=5`. Both IDs match packaged profiles. This verifies configured identity only.

Current remote definitions could not be retrieved: the main workspace `.env` contained no RunningHub credential, and its `state/local/settings.db` contained no provider/settings records when read as an immutable snapshot. Normal read-only SQLite access failed, including an escalated retry. The live server's credential source was not resolved. No authenticated definition request was sent; no remote definition hash is claimed. The [official definition-read documentation](https://www.runninghub.cn/runninghub-api-doc-cn/api-425749014) specifies `POST /api/openapi/getJsonApiFormat`. Wigolo tools were unavailable, so the official page was read with the web fallback.

All SHA-256 values in the contract hash the exact bytes of **local files**, not current provider definitions. In particular the local Ref API definition hashes to `35741b18991ee6a27b1553276c6f1525ee6fd8ca88dc89cc04602a87911058e4`. Profile `api_schema_sha256` and `source_sha256` metadata are not substituted for a fresh remote hash.

## Mode decisions

| Mode | Implementation target | Evidence | Current remote definition verified | Generation verified |
| --- | --- | --- | --- | --- |
| Pure Ref / r2v | Supported | Local saved Ref definition and sanitized contract | No | No |
| First frame / i2v | Supported | Ordinary profile and runtime | No | No |
| First + last / fl2v | Supported | Ordinary profile, runtime, simplified fixture | No | No |
| Multiple segments | Supported within the above local envelopes | Saved Ref segments and ordinary timeline compiler | No | No |
| Ref + first frame | Disabled | Local compiler extension only | No | No |
| Ref + first + last | Disabled | Local compiler extension only | No | No |

`supported=true` means a local implementation target. It is deliberately independent of `definition_verified` and generation `verified`. Unsupported hybrid combinations must fail explicitly; neither references nor frames may be silently removed. A failed prompt compilation must never submit a raw prompt.

The saved Ref graph uses node 12 `MiniMaxH3Director`, `r2v — 参考主体生视频(Reference to Video)`, version 5 and `prompt_batch`. Its global references contain three images and its global generation image is empty. Both active segments have empty generation images and empty segment refs. Thus pure Ref must not require a first frame. There are two segments but three legacy shots and six keyframes in the saved document: their presence is not evidence that mixed reference/frame conditioning is honored remotely. The existing reference compiler requires first frames and emits `Ref-I2V` / `Ref-FL2V`; its own docstring explicitly calls those local contracts pending provider verification. The independent canvas pure-Ref path must not inherit that requirement.

Both profiles bind `12.timeline_data` and identify node 7 `SaveVideo` as video output. The saved Ref graph routes node 6 `CreateVideo` into node 7. Output bindings still require a current remote check before deployment claims.

## Dimensions and limits

The ordinary runtime adds node 18 Refine overrides for width, height, custom aspect ratio (`自定义`) and megapixels only for the packaged ordinary ID. The [September 20 audit](2026-09-20-lighthouse-e1-retest.md) reports a prior authenticated observation of node 12 → node 18, saved 3:4 and 896×1184. This is historical corroboration, not a fresh verification. The local Ref graph has no node 18, so that override must not be assumed for Ref or arbitrary configured replacement IDs.

The live configured reference cap is five; the local compiler accepts a caller cap from one through ten. Ten is not a demonstrated provider maximum. The ordinary timeline uses 24 fps and rounds each segment to `17*k+5` frames. The saved Ref example has totalFrames 180 and segment lengths 107 and 73; saved example values do not establish provider frame/duration limits. Provider maximum frames, references, and segment count remain unknown.

## Validation and follow-up

Parent-run baseline: 144 backend tests passed across asset library, H3 reference payload and H3 timeline; one frontend node-registry test passed; frontend `tsc -b` passed. These checks validate existing local behavior and do not count as generation verification. This evidence-only change is validated by JSON parsing, local source hash comparison, and a secret/private-URL scan before commit.

Before treating the contract as current remote evidence, resolve the live server credential source through its existing credential resolver, fetch both configured definitions with the documented read endpoint, hash them and inspect actual bindings/Refine inputs. Any later paid generation verification must be separately authorized and recorded. Hybrid support stays false until evidence establishes that both reference and frame conditioning are consumed.
