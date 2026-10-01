import { buttonVariants } from '@/components/ui/button';
import { studioLocation, type StudioContext } from './studio-context';
import type { StudioModule } from './studio-api';

export function StudioShortcut({ project, studio, label, ...context }: { project: string; studio: StudioModule; label: string } & Omit<Partial<StudioContext>, 'studio'>) {
  return <a className={buttonVariants({variant:'outline',size:'sm'})} href={studioLocation(project,studio,context)}>{label}</a>;
}
