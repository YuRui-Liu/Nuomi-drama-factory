// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { createLazyFileRoute } from "@tanstack/react-router";
import { ProductionCenter } from "@/features/media-production/ProductionCenter";

function ProductionPage() {
  const { project } = Route.useParams();
  return <ProductionCenter project={project} />;
}

export const Route = createLazyFileRoute("/_app/projects/$project/production")({
  component: ProductionPage,
});
