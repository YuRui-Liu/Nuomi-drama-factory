import { expect, it } from "vitest";
import { characterCardCopy } from "./character-library-data";
it("copies editable character facts without source project media or voice references", () => {
  const copy = characterCardCopy({ name: "源角色", description: "原文事实", face_prompt: "外观", portrait_path: "private/source.png", reference_audio_path: "voice.wav" }, "新角色");
  expect(copy).toEqual({ name: "新角色", description: "原文事实", face_prompt: "外观", role: undefined, gender: undefined, is_main: undefined });
  expect(copy).not.toHaveProperty("portrait_path");
});
