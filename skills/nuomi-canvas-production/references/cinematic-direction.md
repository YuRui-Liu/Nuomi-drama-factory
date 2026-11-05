# Cinematic direction for H3

Use this guide to translate story causality into visible evidence and then into the structured inputs supported by the current H3 route. These are directing choices, not mandatory prompt sections. User facts, saved references, and current runtime capabilities take priority over examples.

## 1. Story cause to visible evidence

For each continuous segment, identify what changes, why it changes, and what the audience can actually see. A motive such as fear is not itself a visible event; evidence might be a hand stopping over a door latch, a gaze moving to the exit, or a prop changing hands, when those facts are in the screenplay or director's plan.

Keep the chosen intent and action order tied to the supplied scene. Do not add characters, props, actions, dialogue, or reactions to make a brief feel complete. If the source does not establish an intent or visible cue, preserve it as unknown and leave it for review.

## 2. Scene context and blocking

Carry forward available scene references, location details, screen positions, camera side, axis, and direction of travel. Add a spatial map only when the source or director explicitly supplies one and it helps the shot. A map need not be repeated as boilerplate in every segment.

For a recurring or axis-sensitive scene, maintain one shared GEO map of supplied landmarks, world orientation, actor/prop positions and paths, axis, camera side, and lighting logic. After each cut, restate only the positions, gaze, facing, and screen direction the next shot needs so the new view remains legible. This adapts the Hell Grind project brief's scene-map / post-cut continuity practice; it does not make a complete map mandatory for every scene or segment. Use the 3D snapshot or scene-blocking contract in [storyboard-direction.md](storyboard-direction.md) when the workspace supports it; otherwise keep the map in the local director document.

When a scene detail or position is missing, mark it as unknown. Do not substitute a generic street, room, lighting plan, or named character. Do not claim that a camera stays on one side of an axis unless that side and axis were specified. Never infer metric distances from a reference image.

## 3. Action and timing

Preserve the supplied action sequence and any explicit timing. A shot duration does not establish when each action happens; do not divide actions into invented timed beats. A strong opening event, establishing wide shot, or pause may help a particular scene, but none is required by default.

Use a continuous action for a continuous clip. If an event cannot fit or read clearly, change the shot design with the user-approved story facts rather than packing in extra events.

## 4. Performance

Use declared performance details such as gaze, posture, expression, breath, gesture, or weight shift. If the production has an approved stable behavior profile, combine it with this scene's goal, obstacle, and tactic to choose a visible action and reaction; carry a reaction into the next shot only when it continues the dramatic cause. This adapts the Hell Grind brief's performance method without making its roles or beats a Nuomi schema. Keep each movement connected to the character's motive and the visible evidence in this scene. Do not add generic blinks, breaths, hand motions, or a fixed cadence of micro-events when none was directed.

If no performance has been specified, keep it unknown. Avoid converting an emotional label into a new physical action unless the director or source provides that action.

## 5. Camera and framing

Compile the selected camera motion, framing, angle, camera side, and screen direction from explicit director inputs. If motion is absent, report that it is unspecified; do not silently choose a static shot, push-in, pan, or subject-following move.

H3 camera terms commonly used by the current guides include Push In, Pull Out, Zoom In, Zoom Out, Pan, Truck, Tilt, Pedestal, Arc Shot, Tracking Shot, Static Shot, Roll, Shake, and POV. Select a term only when the director intends it and the active route supports it. Add direction, speed, or amplitude only when provided or deliberately decided.

## 6. Light, dialogue, and sound

Carry through the declared lighting and sound cues. An empty or missing sound field does not mean wind, metal impacts, footsteps, breathing, or music should be added. Keep music and effects only when the scene calls for them.

Preserve dialogue verbatim in the selected audio route. By default, create it as a separate dubbing track using the character's voice profile, or reuse an existing external independent dubbing track when it matches; keep dialogue out of video generation. Mute or replace dialogue embedded in the video to prevent doubled voices, while retaining environmental sound when available and intended. Include dialogue in video audio only when the user explicitly chooses the original-video-dialogue route. A segment with no dialogue needs no dubbing. Dubbing does not require mouth animation or lip-sync. Add visible speaking motion only when requested and supported by the active route.

## 7. Compile, review, and repair

Follow this sequence:

1. Read the story cause and the supplied start/end states.
2. Identify the visible evidence that expresses the cause and change.
3. Put only those facts into fields the current structured input and H3 compiler actually support. Read [cli.md](cli.md) and [h3-experience.md](h3-experience.md) for the existing preview and preparation path; check runtime capabilities instead of inventing fields or commands.
4. Review the returned prepared input, then watch the generated segment as a continuous clip. Check whether the visible evidence carries the cause through to the declared ending, and whether timing, identities, props, sound, and camera motion match the actual inputs.
5. If a segment misses, identify the specific input that caused the miss, edit that local direction, preview again, and rerun the affected segment through the existing workflow. Change one local instruction at a time and retain the revision note. If repeated targeted repairs still miss, simplify or split the shot instead of endlessly resubmitting it. Retain successful unaffected segments and recheck the final sequence.

Do not treat task completion or a successful export as content approval. Do not add a separate review gate: use the existing screenplay, assets, and storyboard review points in the parent skill.

## 8. Avoid fixed defaults

- No mandatory GEO block on every segment.
- No mandatory wide shot during the first second or strongest event during the first two seconds.
- No timed micro-event cadence.
- No default static camera, environmental effects, music, or generic acting motion.
- Unknown fields remain unknown until a source or director supplies them.
