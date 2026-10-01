import { createLazyFileRoute } from '@tanstack/react-router';
import { StudioPage } from '@/features/studios/studio-page';

export const Route = createLazyFileRoute('/_app/projects/$project/studios')({ component: Studios });
function Studios() {
  const { project } = Route.useParams();
  return <StudioPage key={project} project={project} />;
}
