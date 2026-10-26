import { Router } from "express";
import { z } from "zod";
import u from "@/utils";
import { taskCommandSchema } from "@/utils/production/taskSchema";
import { validateFields } from "@/lib/middleware";
import { success } from "@/lib/responseFormat";

const router = Router();
const bodySchema = z.strictObject({ directory: z.string().min(1).max(4096), command: taskCommandSchema });

export default router.post("/", validateFields(bodySchema.shape), async (req, res) => {
  const { directory, command } = bodySchema.parse(req.body);
  const root = await u.workspace.resolveWorkspace(req, directory);
  res.json(success(await u.productionTasks.executeTask(root, command)));
});
