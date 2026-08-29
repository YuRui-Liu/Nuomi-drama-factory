// SPDX-License-Identifier: Elastic-2.0

export type NarrativeImageSize = "1K" | "2K" | "4K";

const VIP_IMAGE_SIZES: readonly NarrativeImageSize[] = ["1K", "2K", "4K"];
const STANDARD_IMAGE_SIZES: readonly NarrativeImageSize[] = ["1K"];

export function supportedNarrativeImageSizes(model?: string | null): readonly NarrativeImageSize[] {
  return model === "gpt-image-2-vip" ? VIP_IMAGE_SIZES : STANDARD_IMAGE_SIZES;
}

export function defaultNarrativeImageSize(model?: string | null): NarrativeImageSize {
  return model === "gpt-image-2-vip" ? "2K" : "1K";
}

export function coerceNarrativeImageSize(
  model: string | null | undefined,
  imageSize: string | null | undefined,
): NarrativeImageSize {
  const supported = supportedNarrativeImageSizes(model);
  return supported.includes(imageSize as NarrativeImageSize)
    ? imageSize as NarrativeImageSize
    : defaultNarrativeImageSize(model);
}
