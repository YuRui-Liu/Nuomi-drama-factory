import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { CharacterDelivery } from '@/components/assets/character-delivery/character-delivery';
import type { CastingCandidate } from '@/types/character-casting';

const { action, create, state, refetch } = vi.hoisted(() => ({ action: vi.fn(), create: vi.fn(), refetch: vi.fn(),
  state: {workspace: {} as Record<string, unknown>, candidates: [] as CastingCandidate[]},
}));
vi.mock('@/lib/queries/character-casting', () => ({
  useCharacterCasting: () => ({ data: { data: state.workspace }, refetch }),
  useCastingCandidates: () => ({ data: { data: state.candidates }, refetch }),
  useCastingAction: () => ({ mutateAsync: action, isPending: false }),
  castingTaskActive: (t: {status:string}) => t.status === 'running',
}));
vi.mock('@/lib/queries/characters', () => ({ useCreateIdentity: () => ({ mutateAsync: create }) }));
function sheet(id:string): CastingCandidate {
  return {schema_version:1,project_id:'p',character_id:'周野',identity_id:null,task_id:'task',requested_model:null,
    generation_metadata:{},asset_sha256:null,report:null,error:null,review_attempts:[],
    candidate_id:id,sheet_contract:'character_sheet_v4',generation_status:'succeeded',review_status:'completed',
    review_attempt_id:'qc',asset_version:1,crop_box:[0,0,.25,.5],url:'/sheet.png',avatar_url:'/avatar.png',stale:false,
    sheet_quality:{passed:true,issues:[]},snapshot:{schema_version:1,revision_id:'r1',source_revision:'source',style_revision:'style',
      profile_hash:'profile',character_id:'周野',identity_id:null,proposal_id:'a',prompt:'sheet',hard_constraints:[],design_decisions:[],
      style:'写实',proposal_snapshot:{title:id},source_fact_ids:[],interpretations:[],creative_choices:[],snapshot_hash:'hash'},
    adoption_requirements:{blocked_reason:null,expected_review_attempt_id:'qc',required_acknowledgements:[],override_reason_required:false},
  };
}
beforeEach(()=>{
  sessionStorage.clear();
  action.mockReset(); create.mockReset(); refetch.mockReset();
  state.candidates=[];
  state.workspace={can_edit: true, identities: [], tasks: [],
    revision: { revision_id: 'r1' }, selected_proposal_id: null, proposals: [
      { proposal_id: 'a', title: '短装', rationale: '行动方便', outfit_states: { default: '短外套' }, casting_decisions: [] },
      { proposal_id: 'b', title: '长装', rationale: '轮廓鲜明', outfit_states: { default: '长外套' }, casting_decisions: [] },
      { proposal_id: 'c', title: '便装', rationale: '日常', outfit_states: { default: '夹克' }, casting_decisions: [] },
    ]};
});
describe('character delivery', () => {
  it('allows explicit human adoption when QC has suggestions',async()=>{
    const candidate=sheet('new');
    candidate.sheet_quality={passed:false,issues:[{region:'服装',problem:'袖宽差异',suggestion:'可调整'}]};
    candidate.adoption_requirements.override_reason_required=true;
    state.candidates=[candidate]; action.mockResolvedValue({data:{}});
    render(<CharacterDelivery project="p" name="周野"/>);
    fireEvent.click(screen.getByRole('button',{name:'人工通过并采用'}));
    await waitFor(()=>expect(action).toHaveBeenCalledWith(expect.objectContaining({kind:'adopt',body:expect.objectContaining({override_reason:'用户确认当前效果可用，人工通过并采用'})})));
  });
  it('keeps stale proposals selectable and lets the user continue without regenerating',async()=>{
    state.workspace.draft_stale=true;
    state.workspace.proposals=(state.workspace.proposals as unknown[]).slice(0,1);
    state.workspace.selected_proposal_id='a';
    render(<CharacterDelivery project="p" name="周野"/>);
    expect(screen.getByRole('button',{name:/短装/})).toBeEnabled();
    expect(screen.getByRole('button',{name:'确认方案并出图'})).toBeEnabled();
    action.mockResolvedValue({data:{}});
    fireEvent.click(screen.getByRole('button',{name:'确认方案并出图'}));
    await waitFor(()=>expect(action).toHaveBeenCalledWith(expect.objectContaining({kind:'generate',body:expect.objectContaining({reuse_stale_draft:true})})));
  });
  it('compares proposals in place and saves an edited selection before generating', async () => {
    render(<CharacterDelivery project="p" name="周野" />);
    fireEvent.click(screen.getByRole('button', { name: /短装/ }));
    expect(screen.getByText('长装')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('服装与状态'), { target: { value: '灰色短外套' } });
    fireEvent.click(screen.getByRole('button', { name: '保存方案' }));
    expect(action).toHaveBeenCalledWith(expect.objectContaining({ kind: 'draft', body: expect.objectContaining({
      selected_proposal_id: 'a', proposals: expect.arrayContaining([expect.objectContaining({ outfit_states: { default: '灰色短外套' } })]),
    }) }));
  });
  it('confirms edits and uses the saved revision for the sheet request', async()=>{
    action.mockResolvedValueOnce({data:{revision:{revision_id:'r2'}}}).mockResolvedValueOnce({data:{}});
    render(<CharacterDelivery project="p" name="周野" />);
    fireEvent.click(screen.getByRole('button',{name:/短装/}));
    fireEvent.change(screen.getByLabelText('发型'),{target:{value:'短发'}});
    fireEvent.click(screen.getByRole('button',{name:'确认方案并出图'}));
    await waitFor(()=>expect(action).toHaveBeenLastCalledWith(expect.objectContaining({kind:'generate',body:expect.objectContaining({expected_revision:'r2',sheet_contract:'character_sheet_v4'})})));
  });
  it('keeps only progress while making a sheet and restores the result on return',()=>{
    state.workspace.tasks=[{status:'running',request_id:'task'}]; state.candidates=[sheet('new')];
    const first=render(<CharacterDelivery project="p" name="周野" />);
    expect(screen.getByRole('status')).toHaveTextContent('可以继续处理其他角色');
    expect(screen.queryByAltText('候选设定图')).not.toBeInTheDocument();
    first.unmount(); state.workspace.tasks=[];
    render(<CharacterDelivery project="p" name="周野" />);
    expect(screen.getByAltText('候选设定图')).toBeVisible();
  });
  it('does not resurface older candidates after adoption',()=>{
    state.candidates=[sheet('old'),sheet('current')];
    state.workspace.current={candidate_id:'current',url:'/avatar.png',sheet_url:'/sheet.png'};
    render(<CharacterDelivery project="p" name="周野" />);
    expect(screen.getByAltText('当前角色形象')).toBeVisible();
    expect(screen.queryByRole('button',{name:'采用'})).not.toBeInTheDocument();
  });
  it('submits change feedback with the delivered source without selecting proposals again', async()=>{
    state.candidates=[sheet('new')]; action.mockResolvedValue({data:{}});
    render(<CharacterDelivery project="p" name="周野" />);
    fireEvent.click(screen.getByRole('button',{name:'提出修改'}));
    fireEvent.change(screen.getByLabelText('修改意见'),{target:{value:'只修改外套'}});
    fireEvent.click(screen.getByRole('button',{name:'确认修改并出图'}));
    await waitFor(()=>expect(action).toHaveBeenCalledWith(expect.objectContaining({kind:'generate',body:expect.objectContaining({source_candidate_id:'new',change_request:'只修改外套'})})));
    expect(screen.queryByText('选择造型方向')).not.toBeInTheDocument();
  });
  it('does not allow adopting a stale candidate',()=>{
    state.candidates=[{...sheet('old'),stale:true}];
    render(<CharacterDelivery project="p" name="周野" />);
    expect(screen.queryByRole('button',{name:'采用'})).not.toBeInTheDocument();
  });
  it('restores modification feedback after switching away and returning',()=>{
    state.candidates=[sheet('new')];
    const first=render(<CharacterDelivery project="p" name="周野"/>);
    fireEvent.click(screen.getByRole('button',{name:'提出修改'}));
    fireEvent.change(screen.getByLabelText('修改意见'),{target:{value:'保留脸，只换外套'}});
    first.unmount();
    render(<CharacterDelivery project="p" name="周野"/>);
    expect(screen.getByLabelText('修改意见')).toHaveValue('保留脸，只换外套');
  });
  it('creates a variant with the requested description and age',async()=>{
    create.mockResolvedValue({data:{identity_id:'night'}});
    render(<CharacterDelivery project="p" name="周野" />);
    fireEvent.click(screen.getByRole('button',{name:'＋ 新增变体'}));
    fireEvent.change(screen.getByLabelText('变体名称'),{target:{value:'雨夜'}});
    fireEvent.change(screen.getByLabelText('变体要求'),{target:{value:'深色雨衣'}});
    fireEvent.change(screen.getByLabelText('年龄阶段（换装时保持不变）'),{target:{value:'youth'}});
    fireEvent.click(screen.getByRole('button',{name:'创建变体'}));
    await waitFor(()=>expect(create).toHaveBeenCalledWith({identity_name:'雨夜',appearance_details:'深色雨衣',age_group:'youth'}));
  });
});
