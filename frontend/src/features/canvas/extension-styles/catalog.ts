import generatedCatalog from "./catalog.generated.json";

import {
  FRAGMENT_KEYS,
  type ExtensionStyle,
} from "./types";

export { FRAGMENT_KEYS };
export type {
  ExtensionStyle,
  ExtensionStyleCategory,
  ExtensionStyleFragmentKey,
  ExtensionStylePromptFragment,
  ExtensionStyleSource,
} from "./types";

export const EXTENSION_STYLES = generatedCatalog as readonly ExtensionStyle[];

export const EXTENSION_STYLES_BY_ID: Readonly<
  Record<string, ExtensionStyle>
> = Object.fromEntries(EXTENSION_STYLES.map((style) => [style.id, style]));

export function getExtensionStyle(id: string): ExtensionStyle | undefined {
  return EXTENSION_STYLES_BY_ID[id];
}
