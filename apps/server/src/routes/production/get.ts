import { Router } from "express";
import { z } from "zod";
import { productionReadSchema } from "@toonflow/node-shared/production/operations";
import u from "@/utils";
import { validateFields } from "@/lib/middleware";
import { success } from "@/lib/responseFormat";

const router = Router();
const querySchema = z.strictObject({
  directory: z.string().min(1).max(4096),
  request: z.string().max(10000).transform((value, context) => {
    try { return JSON.parse(value); }
    catch { context.addIssue({ code: "custom", message: "request 必须是 JSON" }); return z.NEVER; }
  }).pipe(productionReadSchema),
});

export default router.get("/", validateFields(querySchema.shape, "query"), async (req, res) => {
  const { directory, request } = querySchema.parse(req.query);
  const root = await u.workspace.resolveWorkspace(req, directory);
  res.set("Cache-Control", "no-store").json(success(await u.production.read(root, request)));
});
