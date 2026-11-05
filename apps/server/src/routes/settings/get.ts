import u from "@/utils";
import { Router } from "express";
import { success } from "@/lib/responseFormat";
import { createHash } from "node:crypto";

const router = Router();

export default router.get("/", (req, res) => {
  u.mcpControl.assertAppRequest(req);
  res.set("Cache-Control", "no-store");
  const settings = u.conf.get("settings", {});
  if (u.cleanNodeMenuSettings(settings)) u.conf.set("settings", settings);
  res.set("ETag", `"${createHash("sha256").update(JSON.stringify(settings)).digest("hex")}"`);
  res.json(success(settings));
});
