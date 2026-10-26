import { Router } from "express";
import { z } from "zod";
import { productionExecuteSchema } from "@toonflow/node-shared/production/operations";
import u from "@/utils";
import { validateFields } from "@/lib/middleware";
import { success } from "@/lib/responseFormat";

const router = Router();
const bodySchema = z.strictObject({ directory: z.string().min(1).max(4096), command: productionExecuteSchema });

export default router.post("/", validateFields(bodySchema.shape), async (req, res) => {
  const { directory, command } = bodySchema.parse(req.body);
  const root = await u.workspace.resolveWorkspace(req, directory);
  res.json(success(await u.production.execute(root, command)));
});
