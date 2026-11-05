import { Router } from "express";
import { z } from "zod";
import { assetCatalogCommandSchema } from "@toonflow/node-shared/production/assetCatalog";
import u from "@/utils";
import { validateFields } from "@/lib/middleware";
import { success } from "@/lib/responseFormat";

const schema = z.strictObject({ directory: z.string().min(1).max(4096), command: assetCatalogCommandSchema });
export default Router().post("/", validateFields(schema.shape), async (req, res) => {
  const { directory, command } = schema.parse(req.body);
  const root = await u.workspace.resolveWorkspace(req, directory);
  res.json(success(await u.assetCatalog.executeCatalog(root, command)));
});
