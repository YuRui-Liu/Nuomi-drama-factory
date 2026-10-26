import type { z } from "zod";
import type { adoptionSchema, productionContentSchema, productionManifestSchema, productionRecordSchema, productionRefSchema } from "./schemas";

export type ProductionRef = z.infer<typeof productionRefSchema>;
export type ProductionContent = z.infer<typeof productionContentSchema>;
export type ProductionRecord = z.infer<typeof productionRecordSchema>;
export type ProductionManifest = z.infer<typeof productionManifestSchema>;
export type Adoption = z.infer<typeof adoptionSchema>;
