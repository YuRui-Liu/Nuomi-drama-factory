import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { CharacterVoiceWorkspace } from '@/features/canvas/production/voice/CharacterVoiceWorkspace';
import type { VoiceVersionRecord } from '@/features/canvas/production/voice/voiceNodeBinding';

vi.mock('@/features/canvas/production/workspace/ProductionWorkspaceLayout', () => ({
  ProductionWorkspaceLayout: ({ title, navigation, children, inspector, actions }: {
    title: string; navigation: React.ReactNode; children: React.ReactNode;
    inspector?: React.ReactNode; actions?: React.ReactNode;
  }) => <div role="dialog" aria-label={title}>
    <nav aria-label="工作区导航">{navigation}</nav>
    <main>{children}</main>
    <aside>{inspector}</aside>
    <footer>{actions}</footer>
  </div>,
}));

const versions: VoiceVersionRecord[] = [{
  version_id: 'voice-v1', character_id: 'character-1', identity_id: '', slot: 'default',
  candidate_id: 'candidate-1', audio_sha256: 'hash-1', path: '/voice.wav', url: '/voice.wav', current: true,
}];

it('shows available role voices and uses one action to open the linked audio node', () => {
  const useVersion = vi.fn(() => true);
  const onBack = vi.fn();
  const child = <section>设计音色、导入参考和候选试听继续使用角色声音面板。</section>;
  const props = {
    project: 'project-1',
    characters: [{ name: '林岚', character_id: 'character-1' }, { name: '站台陌生人', character_id: 'character-2' }],
    characterName: '林岚',
    versions,
    onBack,
    onSelectCharacter: vi.fn(),
    onUseVersion: useVersion,
    onRefreshVersions: vi.fn(),
  };
  render(
    <CharacterVoiceWorkspace
      {...props}
    >
      {child}
    </CharacterVoiceWorkspace>,
  );

  expect(screen.getByRole('dialog', { name: '角色声音' })).toBeInTheDocument();
  expect(screen.getByRole('navigation', { name: '角色选择' })).toHaveTextContent('站台陌生人');
  expect(screen.getByRole('navigation', { name: '角色选择' })).not.toHaveTextContent('project-1');
  expect(screen.getByRole('main')).toHaveTextContent('候选试听继续使用角色声音面板');
  expect(screen.getByRole('region', { name: '当前绑定' })).toBeInTheDocument();
  expect(screen.getByText('default · 当前声音')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: '使用此声音' }));
  expect(useVersion).toHaveBeenCalledWith(versions[0]);
  expect(onBack).toHaveBeenCalledTimes(1);
});

it('shows how to create a role voice when no adopted version exists', () => {
  render(
    <CharacterVoiceWorkspace
      project="project-1"
      characters={[{ name: '林岚', character_id: 'character-1' }]}
      characterName="林岚"
      versions={[]}
      onBack={vi.fn()}
      onSelectCharacter={vi.fn()}
      onUseVersion={vi.fn()}
      onRefreshVersions={vi.fn()}
    >
      <section>声音设计</section>
    </CharacterVoiceWorkspace>,
  );

  expect(screen.queryByRole('button', { name: '使用此声音' })).not.toBeInTheDocument();
  expect(screen.getByText('还没有可用声音。可在中间的角色声音面板中设计、克隆或导入。')).toBeInTheDocument();
});

it('marks a missing bound version stale and offers refresh before reconnecting', () => {
  const refresh = vi.fn();
  render(
    <CharacterVoiceWorkspace
      project="project-1"
      characters={[{ name: '林岚', character_id: 'character-1' }]}
      characterName="林岚"
      boundVersionId="removed-version"
      versions={versions}
      onBack={vi.fn()}
      onSelectCharacter={vi.fn()}
      onUseVersion={vi.fn()}
      onRefreshVersions={refresh}
    >
      <section>声音设计</section>
    </CharacterVoiceWorkspace>,
  );

  expect(screen.getByRole('alert')).toHaveTextContent('上次选用的声音当前不可用');
  fireEvent.click(screen.getByRole('button', { name: '刷新声音版本' }));
  expect(refresh).toHaveBeenCalledTimes(1);
});

it('keeps an audition candidate separate until the user chooses an available voice', () => {
  const onUseVersion = vi.fn();
  const auditionCandidate = { candidate_id: 'candidate-2', character_id: 'character-1', url: '/candidate.wav' };
  render(
    <CharacterVoiceWorkspace
      project="project-1"
      characters={[{ name: '林岚', character_id: 'character-1' }]}
      characterName="林岚"
      versions={versions}
      onBack={vi.fn()}
      onSelectCharacter={vi.fn()}
      onUseVersion={onUseVersion}
      onRefreshVersions={vi.fn()}
    >
      <button type="button" onClick={() => undefined} aria-label="播放试听候选">试听候选</button>
      <span>{auditionCandidate.candidate_id} · 尚未采用</span>
    </CharacterVoiceWorkspace>,
  );

  fireEvent.click(screen.getByRole('button', { name: '播放试听候选' }));
  expect(onUseVersion).not.toHaveBeenCalled();
  expect(screen.getByText('candidate-2 · 尚未采用')).toBeInTheDocument();
});
