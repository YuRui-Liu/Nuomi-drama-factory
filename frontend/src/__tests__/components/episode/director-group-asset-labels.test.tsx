import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { GroupEditor } from "@/components/episode/director-review/group-editor";
import type { DirectorPlanRevision } from "@/lib/queries/director-plans";

it("keeps asset identity visible alongside shot-specific visible changes", () => {
  const revision = { groups: [{ id: "g1", objective: "冲突", shots: [{ id: "s1", action: "取出药瓶", asset_requirements: [
    { kind: "prop", entity_key: "药瓶", visible_change: "从岑砚外套口袋取出" },
    { kind: "prop", entity_key: "水桶", visible_change: "撞击后轻轻颤动" },
    { kind: "scene", entity_key: "院落" },
  ] }] }] } as DirectorPlanRevision;
  const onCommand = vi.fn();
  render(<GroupEditor revision={revision} onCommand={onCommand} />);
  expect(screen.getByText(/资产需求：/)).toHaveTextContent("prop:药瓶（从岑砚外套口袋取出）；prop:水桶（撞击后轻轻颤动）；scene:院落");
  expect(onCommand).not.toHaveBeenCalled();
});
