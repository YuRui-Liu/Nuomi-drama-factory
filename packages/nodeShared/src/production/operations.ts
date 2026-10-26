import { z } from "zod";
import { adoptionSchema, productionContentSchema, productionIdSchema, productionRefSchema } from "./schemas";

export const productionReadSchema = z.discriminatedUnion("action", [
  z.strictObject({ action: z.literal("list"), offset: z.number().int().nonnegative().default(0), limit: z.number().int().min(1).max(100).default(20) }),
  z.strictObject({ action: z.literal("read"), ref: productionRefSchema }),
  z.strictObject({ action: z.literal("listAffected"), id: productionIdSchema }),
]);

export const productionExecuteSchema = z.discriminatedUnion("action", [
  z.strictObject({
    action: z.literal("saveRevision"), id: productionIdSchema.optional(), expectedRevision: z.number().int().nonnegative(),
    episodeId: productionIdSchema.optional(), dependencies: z.array(productionRefSchema).max(1000), content: productionContentSchema,
  }),
  z.strictObject({
    action: z.literal("adopt"), ref: productionRefSchema,
    expectedAdoption: productionRefSchema.nullable(), approval: adoptionSchema.shape.approval,
  }),
]);

export type ProductionRead = z.infer<typeof productionReadSchema>;
export type ProductionExecute = z.infer<typeof productionExecuteSchema>;
