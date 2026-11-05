import { z } from "zod";
import { productionPathSchema } from "./schemas";

const id = z.string().regex(/^[a-f0-9]{32}$/);
const text = z.string().trim().min(1).max(10000);
export const assetVersionRefSchema = z.strictObject({ assetId: id, version: z.number().int().positive() });
const approval = z.strictObject({ source: z.enum(["user", "delegated"]), evidence: text });
export const assetCatalogCommandSchema = z.discriminatedUnion("action", [
  z.strictObject({ action: z.literal("saveWork"), expectedRevision: z.number().int().nonnegative(), workId: id.optional(), title: text }),
  z.strictObject({
    action: z.literal("saveVersion"), expectedRevision: z.number().int().nonnegative(), workId: id, assetId: id.optional(),
    name: text, kind: z.enum(["character", "characterVariant", "scene", "sceneVariant", "prop", "voice"]),
    reason: text, sourcePath: productionPathSchema,
    relations: z.array(z.strictObject({ relation: z.enum(["variantOf", "voiceOf", "derivedFrom"]), target: assetVersionRefSchema })).max(20),
  }),
  z.strictObject({ action: z.literal("adoptVersion"), expectedRevision: z.number().int().nonnegative(), ref: assetVersionRefSchema, approval }),
  z.strictObject({ action: z.literal("useVersion"), expectedRevision: z.number().int().nonnegative(), workId: id, ref: assetVersionRefSchema, approval }),
]);
export const assetCatalogSchema = z.strictObject({
  revision: z.number().int().nonnegative(),
  works: z.array(z.strictObject({ id, title: text, directory: z.string().min(1) })).max(10000),
  assets: z.array(z.strictObject({
    id, ownerWorkId: id, kind: z.enum(["character", "characterVariant", "scene", "sceneVariant", "prop", "voice"]),
    versions: z.array(z.strictObject({
      version: z.number().int().positive(), name: text, reason: text, path: productionPathSchema,
      sourcePath: productionPathSchema, hash: z.string().regex(/^[a-f0-9]{64}$/), createdAt: z.iso.datetime(),
      relations: z.array(z.strictObject({ relation: z.enum(["variantOf", "voiceOf", "derivedFrom"]), target: assetVersionRefSchema })),
      approval: approval.optional(),
    })).min(1),
  })).max(10000),
  uses: z.array(z.strictObject({ workId: id, ref: assetVersionRefSchema, approval })).max(100000),
});
export type AssetCatalog = z.infer<typeof assetCatalogSchema>;
