import pytest


@pytest.fixture
async def store(tmp_path):
    from novelvideo.script_creation.store import DocumentStore
    result = DocumentStore(tmp_path / "longform.db")
    await result.initialize()
    return result


def outline_items():
    from novelvideo.script_creation.models import OutlineItem
    return [
        OutlineItem("series", None, 1, "主线", "series_arc"),
        OutlineItem("arc-a", "series", 1, "第一阶段", "story_arc", 1, 30),
        *[OutlineItem(f"ep-{n}", "arc-a", n, f"第{n}集", "episode", n, n)
          for n in range(1, 101)],
    ]


def block_locator(document, index=0):
    return f"block:{document.revision.blocks[index].id}"


async def adopt_source(store, document, mutation_id):
    return await store.adopt_revision(document.id, revision_id=document.current_revision_id,
                                      client_mutation_id=mutation_id)


async def test_outline_revision_persists_100_episodes_and_stable_order(store):
    outline = await store.create_outline(title="长篇大纲", items=outline_items(),
                                          client_mutation_id="outline-create")
    assert [item.id for item in outline.revision.outline_items[2:]][-1] == "ep-100"
    moved = [outline.revision.outline_items[0], outline.revision.outline_items[1],
             *reversed(outline.revision.outline_items[2:])]
    saved = await store.save_outline(outline.id, base_revision_id=outline.current_revision_id,
                                     items=moved, client_mutation_id="outline-reorder")
    assert [item.id for item in saved.revision.outline_items[2:]][:2] == ["ep-100", "ep-99"]
    assert saved.revision.outline_items[-1].id == "ep-1"


async def test_outline_rejects_invalid_hierarchy(store):
    from novelvideo.script_creation.models import OutlineItem
    with pytest.raises(ValueError):
        await store.create_outline(title="坏大纲", items=[
            OutlineItem("a", "b", 1, "A", "story_arc"),
            OutlineItem("b", "a", 1, "B", "story_arc"),
        ], client_mutation_id="invalid-outline")


async def test_outline_structure_write_failure_rolls_back_document_revision(store, monkeypatch):
    outline = await store.create_outline(title="长篇大纲", items=outline_items(),
                                          client_mutation_id="outline-atomic-create")

    async def fail_after_revision(*args, **kwargs):
        raise RuntimeError("outline persistence failed")

    monkeypatch.setattr(store, "_persist_outline", fail_after_revision)
    moved = list(outline.revision.outline_items)
    moved[2], moved[3] = moved[3], moved[2]
    with pytest.raises(RuntimeError, match="outline persistence failed"):
        await store.save_outline(outline.id, base_revision_id=outline.current_revision_id,
                                 items=moved, client_mutation_id="outline-atomic-save")
    current = await store.get(outline.id)
    assert current.current_revision_id == outline.current_revision_id
    assert len(await store.revisions(outline.id)) == 1


async def test_outline_create_replay_binds_mutation_to_hierarchy(store):
    original_items = outline_items()
    original = await store.create_outline(title="长篇大纲", items=original_items,
                                           client_mutation_id="outline-replay")
    changed_items = list(original_items)
    from novelvideo.script_creation.models import OutlineItem
    changed_items[2] = OutlineItem("different-episode-id", "arc-a", 1, "第1集", "episode", 1, 1)
    from novelvideo.script_creation.store import DocumentConflict
    with pytest.raises(DocumentConflict):
        await store.create_outline(title="长篇大纲", items=changed_items,
                                   client_mutation_id="outline-replay")
    assert len(await store.list()) == 1
    assert (await store.get(original.id)).revision.outline_items[2].id == "ep-1"


def test_episode_context_filters_candidates_and_future_facts():
    from novelvideo.script_creation.continuity import build_episode_context
    from novelvideo.script_creation.models import ContinuityFact
    facts = [
        ContinuityFact("known", "hero", "owns", "key", "confirmed", 1, 2,
                       "bible", "rev-1", "block:hero-block"),
        ContinuityFact("planned", "villain", "knows", "secret", "planned", 5, 8,
                       "bible", "rev-2", "block:reveal-block"),
        ContinuityFact("candidate", "hero", "is", "spy", "candidate", 1, 1,
                       "bible", "rev-3", "block:candidate-block"),
    ]
    result = build_episode_context(facts, episode=2)
    assert [fact.id for fact in result] == ["known"]
    assert result[0].source_revision_id == "rev-1"


async def test_adopting_continuity_patch_creates_new_bible_revision(store):
    bible = await store.create(kind="brief", title="故事圣经", markdown="初始设定",
                               client_mutation_id="bible-create")
    source = await store.create(kind="episode_script", title="第1集", episode_number=1,
                                markdown="主角拾起旧钥匙。", client_mutation_id="episode-create")
    await adopt_source(store, source, "episode-adopt")
    candidate = await store.create_continuity_candidate(
        bible.id, base_revision_id=bible.current_revision_id,
        patch="新增：主角持有旧钥匙。", source_document_id=source.id,
        source_revision_id=source.current_revision_id, source_locator=block_locator(source),
        episode_start=1, episode_end=1, client_mutation_id="patch-propose")
    assert candidate["status"] == "candidate"
    adopted = await store.adopt_continuity_candidate(
        candidate["id"], client_mutation_id="patch-adopt")
    assert adopted.revision.id != bible.current_revision_id
    assert adopted.revision.markdown.endswith("新增：主角持有旧钥匙。")
    assert (await store.get_continuity_candidate(candidate["id"]))["status"] == "adopted"


async def test_adopting_continuity_candidate_is_idempotent(store):
    bible = await store.create(kind="brief", title="故事圣经", markdown="初始设定",
                               client_mutation_id="adopt-idempotent-bible")
    source = await store.create(kind="episode_script", title="第1集", episode_number=1,
                                markdown="旧钥匙", client_mutation_id="adopt-idempotent-source")
    await adopt_source(store, source, "adopt-idempotent-source-adoption")
    candidate = await store.create_continuity_candidate(
        bible.id, base_revision_id=bible.current_revision_id, patch="发现旧钥匙。",
        source_document_id=source.id, source_revision_id=source.current_revision_id,
        source_locator=block_locator(source), episode_start=1, episode_end=1,
        client_mutation_id="adopt-idempotent-proposal")
    first = await store.adopt_continuity_candidate(candidate["id"],
                                                   client_mutation_id="adopt-idempotent")
    second = await store.adopt_continuity_candidate(candidate["id"],
                                                    client_mutation_id="adopt-idempotent")
    assert second.current_revision_id == first.current_revision_id
    assert len(await store.revisions(bible.id)) == 2


async def test_candidate_adoption_failure_rolls_back_bible_revision(store, monkeypatch):
    bible = await store.create(kind="brief", title="故事圣经", markdown="初始设定",
                               client_mutation_id="atomic-bible")
    source = await store.create(kind="episode_script", title="第1集", episode_number=1,
                                markdown="旧钥匙", client_mutation_id="atomic-source")
    await adopt_source(store, source, "atomic-source-adoption")
    candidate = await store.create_continuity_candidate(
        bible.id, base_revision_id=bible.current_revision_id, patch="发现旧钥匙。",
        source_document_id=source.id, source_revision_id=source.current_revision_id,
        source_locator=block_locator(source), episode_start=1, episode_end=1,
        client_mutation_id="atomic-proposal")

    async def fail_candidate_status(*args, **kwargs):
        raise RuntimeError("adoption receipt failed")

    monkeypatch.setattr(store, "_set_candidate_adopted", fail_candidate_status)
    with pytest.raises(RuntimeError, match="adoption receipt failed"):
        await store.adopt_continuity_candidate(candidate["id"], client_mutation_id="atomic-adopt")
    assert (await store.get(bible.id)).current_revision_id == bible.current_revision_id
    assert (await store.get_continuity_candidate(candidate["id"]))["status"] == "candidate"


async def test_continuity_facts_persist_candidates_and_filter_context_by_episode(store):
    bible = await store.create(kind="brief", title="故事圣经", markdown="设定",
                               client_mutation_id="fact-bible")
    source = await store.create(kind="episode_script", title="第2集", episode_number=2,
                                markdown="她藏起钥匙", client_mutation_id="fact-source")
    await adopt_source(store, source, "fact-source-adoption")
    candidate = await store.create_continuity_fact(
        bible.id, subject_id="hero", predicate="owns", object_id="key", state="planned",
        episode_start=2, episode_end=2, source_document_id=source.id,
        source_revision_id=source.current_revision_id, source_locator=block_locator(source),
        client_mutation_id="fact-candidate")
    future = await store.create_continuity_fact(
        bible.id, subject_id="villain", predicate="knows", object_id="secret", state="planned",
        episode_start=6, episode_end=8, source_document_id=source.id,
        source_revision_id=source.current_revision_id, source_locator=block_locator(source),
        client_mutation_id="fact-future")
    assert candidate.adoption_status == "candidate"
    assert future.adoption_status == "candidate"
    assert (await store.continuity_context(episode=2)) == ()
    adopted = await store.adopt_continuity_fact(candidate.id, client_mutation_id="fact-adopt")
    await store.adopt_continuity_fact(future.id, client_mutation_id="fact-future-adopt")
    context = await store.continuity_context(episode=2)
    assert [fact.id for fact in context] == [candidate.id]
    assert context[0].state == "planned"
    assert context[0].source_document_id == source.id
    assert context[0].source_revision_id == source.current_revision_id
    assert adopted.adoption_status == "adopted"


async def test_continuity_fact_mutations_are_idempotent_and_atomic(store, monkeypatch):
    bible = await store.create(kind="brief", title="故事圣经", markdown="设定",
                               client_mutation_id="fact-atomic-bible")
    source = await store.create(kind="episode_script", title="第1集", episode_number=1,
                                markdown="他听见钟声", client_mutation_id="fact-atomic-source")
    await adopt_source(store, source, "fact-atomic-source-adoption")
    args = dict(subject_id="hero", predicate="heard", object_id="bell", state="confirmed",
        episode_start=1, episode_end=1, source_document_id=source.id,
        source_revision_id=source.current_revision_id, source_locator=block_locator(source),
        client_mutation_id="fact-atomic-candidate")
    fact = await store.create_continuity_fact(bible.id, **args)
    assert await store.create_continuity_fact(bible.id, **args) == fact
    async def fail(*args, **kwargs):
        raise RuntimeError("fact adoption failed")
    monkeypatch.setattr(store, "_mark_continuity_fact_adopted", fail)
    with pytest.raises(RuntimeError, match="fact adoption failed"):
        await store.adopt_continuity_fact(fact.id, client_mutation_id="fact-atomic-adopt")
    assert (await store.get_continuity_fact(fact.id)).adoption_status == "candidate"


async def test_continuity_fact_mutation_replay_rejects_changed_payload(store):
    from novelvideo.script_creation.store import DocumentConflict
    bible = await store.create(kind="brief", title="故事圣经", markdown="设定",
                               client_mutation_id="fact-replay-bible")
    source = await store.create(kind="episode_script", title="第1集", episode_number=1,
                                markdown="线索", client_mutation_id="fact-replay-source")
    await adopt_source(store, source, "fact-replay-source-adoption")
    args = dict(subject_id="hero", predicate="knows", object_id="secret", state="planned",
        episode_start=1, episode_end=1, source_document_id=source.id,
        source_revision_id=source.current_revision_id, source_locator=block_locator(source),
        client_mutation_id="fact-replay")
    await store.create_continuity_fact(bible.id, **args)
    with pytest.raises(DocumentConflict):
        await store.create_continuity_fact(bible.id, **{**args, "object_id": "different"})


async def test_fact_locator_must_resolve_inside_the_referenced_revision(store):
    from novelvideo.script_creation.store import DocumentValidation
    bible = await store.create(kind="brief", title="圣经", markdown="设定",
                               client_mutation_id="locator-bible")
    source = await store.create(kind="episode_script", title="第1集", episode_number=1,
                                markdown="## 场景\n她打开盒子", client_mutation_id="locator-source")
    with pytest.raises(DocumentValidation, match="source locator"):
        await store.create_continuity_fact(
            bible.id, subject_id="hero", predicate="opened", object_id="box", state="occurred",
            episode_start=1, episode_end=1, source_document_id=source.id,
            source_revision_id=source.current_revision_id, source_locator="block:missing-block",
            client_mutation_id="locator-fact")


async def test_fact_locator_rejects_whitespace_around_block_id(store):
    from novelvideo.script_creation.store import DocumentValidation
    bible = await store.create(kind="brief", title="圣经", markdown="设定",
                               client_mutation_id="locator-whitespace-bible")
    source = await store.create(kind="episode_script", title="第1集", episode_number=1,
                                markdown="主角拿起钥匙", client_mutation_id="locator-whitespace-source")
    block_id = source.revision.blocks[0].id
    with pytest.raises(DocumentValidation, match="source locator"):
        await store.create_continuity_fact(
            bible.id, subject_id="hero", predicate="owns", object_id="key", state="confirmed",
            episode_start=1, episode_end=1, source_document_id=source.id,
            source_revision_id=source.current_revision_id, source_locator=f"block: {block_id} ",
            client_mutation_id="locator-whitespace-fact")


async def test_fact_cannot_be_adopted_from_a_stale_unadopted_source_revision(store):
    from novelvideo.script_creation.store import DocumentConflict
    bible = await store.create(kind="brief", title="圣经", markdown="设定",
                               client_mutation_id="source-stale-bible")
    source = await store.create(kind="episode_script", title="第1集", episode_number=1,
                                markdown="主角听见钟声", client_mutation_id="source-stale-script")
    locator = f"block:{source.revision.blocks[0].id}"
    fact = await store.create_continuity_fact(
        bible.id, subject_id="hero", predicate="heard", object_id="bell", state="occurred",
        episode_start=1, episode_end=1, source_document_id=source.id,
        source_revision_id=source.current_revision_id, source_locator=locator,
        client_mutation_id="source-stale-fact")
    await store.adopt_revision(source.id, revision_id=source.current_revision_id,
                               client_mutation_id="source-stale-adopt-old")
    latest = await store.save(source.id, base_revision_id=source.current_revision_id,
                              markdown="主角没有听见钟声", client_mutation_id="source-stale-edit")
    await store.adopt_revision(source.id, revision_id=latest.current_revision_id,
                               client_mutation_id="source-stale-adopt-new")
    with pytest.raises(DocumentConflict, match="source revision is no longer adopted"):
        await store.adopt_continuity_fact(fact.id, client_mutation_id="source-stale-fact-adopt")
    assert (await store.get_continuity_fact(fact.id)).adoption_status == "candidate"
    assert await store.continuity_context(episode=1) == ()


async def test_context_drops_facts_when_their_source_revision_is_no_longer_adopted(store):
    bible = await store.create(kind="brief", title="圣经", markdown="设定",
                               client_mutation_id="context-expiry-bible")
    source = await store.create(kind="episode_script", title="第1集", episode_number=1,
                                markdown="主角看见地图", client_mutation_id="context-expiry-source")
    locator = block_locator(source)
    await store.adopt_revision(source.id, revision_id=source.current_revision_id,
                               client_mutation_id="context-expiry-adopt-old")
    fact = await store.create_continuity_fact(
        bible.id, subject_id="hero", predicate="saw", object_id="map", state="occurred",
        episode_start=1, episode_end=2, source_document_id=source.id,
        source_revision_id=source.current_revision_id, source_locator=locator,
        client_mutation_id="context-expiry-fact")
    await store.adopt_continuity_fact(fact.id, client_mutation_id="context-expiry-fact-adopt")
    assert [item.id for item in await store.continuity_context(episode=1)] == [fact.id]
    latest = await store.save(source.id, base_revision_id=source.current_revision_id,
                              markdown="主角没有看见地图", client_mutation_id="context-expiry-edit")
    await store.adopt_revision(source.id, revision_id=latest.current_revision_id,
                               client_mutation_id="context-expiry-adopt-new")
    assert await store.continuity_context(episode=1) == ()


async def test_patch_adoption_rechecks_source_revision_before_bible_write(store):
    from novelvideo.script_creation.store import DocumentConflict
    bible = await store.create(kind="brief", title="圣经", markdown="设定",
                               client_mutation_id="patch-source-bible")
    source = await store.create(kind="episode_script", title="第1集", episode_number=1,
                                markdown="主角拿到钥匙", client_mutation_id="patch-source-script")
    patch = await store.create_continuity_candidate(
        bible.id, base_revision_id=bible.current_revision_id, patch="主角拥有钥匙。",
        source_document_id=source.id, source_revision_id=source.current_revision_id,
        source_locator=f"block:{source.revision.blocks[0].id}",
        episode_start=1, episode_end=1, client_mutation_id="patch-source-candidate")
    await store.adopt_revision(source.id, revision_id=source.current_revision_id,
                               client_mutation_id="patch-source-adopt-old")
    latest = await store.save(source.id, base_revision_id=source.current_revision_id,
                              markdown="主角没有拿到钥匙", client_mutation_id="patch-source-edit")
    await store.adopt_revision(source.id, revision_id=latest.current_revision_id,
                               client_mutation_id="patch-source-adopt-new")
    with pytest.raises(DocumentConflict, match="source revision is no longer adopted"):
        await store.adopt_continuity_candidate(patch["id"], client_mutation_id="patch-source-adopt")
    assert (await store.get(bible.id)).current_revision_id == bible.current_revision_id
    assert (await store.get_continuity_candidate(patch["id"]))["status"] == "candidate"


def test_context_includes_active_episode_ranges_and_retraction_events():
    from novelvideo.script_creation.continuity import build_episode_context
    from novelvideo.script_creation.models import ContinuityFact
    facts = [
        ContinuityFact("active", "hero", "knows", "map", "planned", 2, 5,
                       "script", "rev-a", "block:b1", "adopted"),
        ContinuityFact("retracted", "hero", "owns", "key", "retracted", 2, 2,
                       "script", "rev-b", "block:b2", "adopted"),
    ]
    assert {fact.id for fact in build_episode_context(facts, episode=3)} == {"active", "retracted"}
    assert {fact.id for fact in build_episode_context(facts, episode=6)} == {"retracted"}
