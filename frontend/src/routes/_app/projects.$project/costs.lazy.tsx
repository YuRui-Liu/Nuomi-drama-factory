// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { createLazyFileRoute } from '@tanstack/react-router';
import { ProjectCostPage } from '@/features/costs/project-cost-page';
function CostsPage() { const { project } = Route.useParams(); return <ProjectCostPage project={project} />; }
export const Route = createLazyFileRoute('/_app/projects/$project/costs')({ component: CostsPage });
