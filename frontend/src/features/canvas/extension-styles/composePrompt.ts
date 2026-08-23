import {
  FRAGMENT_KEYS,
  getExtensionStyle,
} from "./catalog";

export function composeImagePrompt(
  userPrompt: string,
  extensionStyleId?: string | null,
): string {
  if (!extensionStyleId) return userPrompt;

  const style = getExtensionStyle(extensionStyleId);
  if (!style) return userPrompt;

  const fragment = FRAGMENT_KEYS.flatMap((key) =>
    style.prompt_fragment[key].map((phrase) => phrase.trim()).filter(Boolean),
  ).join(", ");
  const suffix = `[Extension style: ${style.name}]\n${fragment}`;

  return userPrompt === "" ? suffix : `${userPrompt}\n\n${suffix}`;
}
