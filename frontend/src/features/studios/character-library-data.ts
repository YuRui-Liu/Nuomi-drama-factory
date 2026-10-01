import type { Character } from "@/types/character";
export function characterCardCopy(source: Character, name: string) {
  return { name: name.trim(), description: source.description, face_prompt: source.face_prompt, role: source.role, gender: source.gender, is_main: source.is_main };
}
