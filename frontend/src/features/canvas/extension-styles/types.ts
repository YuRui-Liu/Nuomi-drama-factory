export const FRAGMENT_KEYS = [
  "medium",
  "rendering",
  "lighting",
  "color",
  "camera",
  "constraints",
] as const;

export type ExtensionStyleFragmentKey = (typeof FRAGMENT_KEYS)[number];

export type ExtensionStyleCategory =
  | "2d"
  | "3d"
  | "realistic"
  | "chinese"
  | "experimental";

export type ExtensionStylePromptFragment = Readonly<
  Record<ExtensionStyleFragmentKey, readonly string[]>
>;

export interface ExtensionStyleSource {
  readonly repository: string;
  readonly source_ids: readonly string[];
  readonly license_review: "approved";
  readonly imported_revision: string;
  readonly [key: string]: unknown;
}

export interface ExtensionStyle {
  readonly id: `drama_ext.${string}`;
  readonly name: string;
  readonly category: ExtensionStyleCategory;
  readonly summary: string;
  readonly prompt_fragment: ExtensionStylePromptFragment;
  readonly use_cases: readonly string[];
  readonly preview_asset: `/images/extension-styles/${string}.webp`;
  readonly source: ExtensionStyleSource;
  readonly version: string;
}
