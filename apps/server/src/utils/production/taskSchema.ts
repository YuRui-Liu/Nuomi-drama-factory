import { z } from "zod";
import { audioGenerationSchema, imageGenerationSchema, videoGenerationSchema } from "@toonflow/tool-media-generation/runtime";
import { adoptionSchema, productionPathSchema, productionRefSchema } from "@toonflow/node-shared/production/schemas";

const taskId = z.string().regex(/^task[a-f0-9]{32}$/);
export const taskRequestSchema = z.discriminatedUnion("type", [
  z.strictObject({ type: z.literal("image"), request: imageGenerationSchema }),
  z.strictObject({ type: z.literal("video"), request: videoGenerationSchema }),
  z.strictObject({ type: z.literal("audio"), request: audioGenerationSchema }),
]);
export const taskCommandSchema = z.discriminatedUnion("action", [
  z.strictObject({ action: z.literal("prepare"), requestId: z.string().regex(/^[a-z][a-zA-Z0-9]{0,95}$/), plan: productionRefSchema, input: taskRequestSchema }),
  z.strictObject({ action: z.literal("submit"), taskId, approval: adoptionSchema.shape.approval }),
  z.strictObject({ action: z.literal("read"), taskId }),
  z.strictObject({ action: z.literal("refresh"), taskId }),
  z.strictObject({ action: z.literal("cancel"), taskId }),
]);
export const taskSchema = z.strictObject({
  id: taskId, requestId: z.string(), plan: productionRefSchema, input: taskRequestSchema,
  providerRevision: z.string(), configurationHash: z.string(),
  status: z.enum(["prepared", "submitting", "running", "downloading", "completed", "failed", "cancelRequested", "cancelled", "needsReview"]),
  providerTaskId: z.string().min(1).max(256).optional(),
  approval: adoptionSchema.shape.approval.optional(),
  references: z.array(z.strictObject({ path: productionPathSchema, hash: z.string() })),
  outputs: z.array(z.strictObject({ path: productionPathSchema, mimeType: z.string(), mediaType: z.enum(["image", "video", "audio"]) })),
  createdAt: z.iso.datetime(), updatedAt: z.iso.datetime(), message: z.string().optional(),
});
export type ProductionTask = z.infer<typeof taskSchema>;
