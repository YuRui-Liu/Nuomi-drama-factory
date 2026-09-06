import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import enTranslation from "../../../../../public/locales/en/translation.json";
import zhTranslation from "../../../../../public/locales/zh/translation.json";
import { NarrativeGroupWorkbench } from "@/components/episode/narrative-workbench/narrative-group-workbench";
import type { NarrativeGroupGenerationSelection } from "@/lib/queries/narrative-groups";

const m = vi.hoisted(() => ({
 mutate: vi.fn(),
 start: vi.fn(),
 refetch: vi.fn(),
 success: vi.fn(),
 error: vi.fn(),
 generateVideo: vi.fn(),
 generateVideoSegment: vi.fn(),
 updateVideoSettings: vi.fn(),
 groupsRefetch: vi.fn(),
 referencePreviewQuery: vi.fn(),
 stageProps: vi.fn(),
 promptsQuery: vi.fn(),
 updateDefaults: vi.fn(),
 updateProject: vi.fn(),
 setOrientation: vi.fn(),
 orientation: "landscape" as "portrait" | "landscape",
 dialogSelection: {useStyle:true,selectedCharacterReferenceIds:["c1"],selectedSceneReferenceIds:[],imageSize:"1K"} as NarrativeGroupGenerationSelection,
 mediaDefaults: {video_model:"newapi_seedance-1.0-pro-fast",h3_mode:"auto",narrative_sketch_provider:"grsai-main",narrative_sketch_model:"nano-banana-2",narrative_render_provider:"grsai-main",narrative_render_model:"gpt-image-2",narrative_render_image_size:"1K"},
 videoModels: [{id:"runninghub:minimax-h3",label:"RunningHub MiniMax H3",provider:"runninghub",available:true,supported_modes:["auto","i2va","fl2va"],default_mode:"auto"}] as any[],
 groupsLoading: false,
 groups: [] as any[],
}));
const group = { id:"g1", ordinal:1, title:"G", beat_ids:["1"], layout:{rows:1,columns:1,capacity:1}, stages:{sketch:{status:"pending",revision:0},render:{status:"partial_failure",revision:0},video:{status:"pending",revision:0}}, cell_to_beat:[],errors:[],video_inputs:[] };
const group2 = { ...group, id: "g2", ordinal: 2, title: "G2" };
vi.mock("@/lib/queries/narrative-groups",()=>({
 useNarrativeGroups:()=>({data:{ok:true,data:m.groups},isLoading:m.groupsLoading,refetch:m.groupsRefetch}),
 useNarrativeGroupAction:()=>({mutateAsync:m.mutate,isPending:false}),
 useNarrativeGroupReferences:()=>({data:{ok:true,data:{style:{id:"s",label:"动漫",prompt:"anime",enabled_by_default:true},character_references:[],scene_references:[],limits:{max_images:9,selected_images:0,omitted_reference_ids:[]},warnings:[]}},isLoading:false,error:null,refetch:m.refetch}),
 useGenerateNarrativeGroupVideo:()=>({mutateAsync:m.generateVideo}),
 useNarrativeGroupVideoReferencePreview:(...args:any[])=>m.referencePreviewQuery(...args),
 useGenerateNarrativeGroupVideoSegment:()=>({mutateAsync:m.generateVideoSegment}),
 useChangeNarrativeGroupStyle:()=>({mutateAsync:vi.fn(),isPending:false}),
 useNarrativeGroupVideoPrompts:(...args:any[])=>m.promptsQuery(...args),
 useUpdateNarrativeGroupVideoDialogueSource:()=>({mutateAsync:vi.fn()}),
 updateNarrativeGroupVideoPlan:vi.fn().mockResolvedValue({ok:true}),
 updateNarrativeGroupVideoSettings:(...args:any[])=>m.updateVideoSettings(...args),
 narrativeGroupTaskScope:()=>"grid-scope",
 narrativeGroupVideoTaskScope:()=>"video-scope",
 narrativeGroupVideoPromptUnitKey:(_:any,index:number)=>String(index),
}));
vi.mock("@/lib/queries/styles",()=>({useStyles:()=>({data:{ok:true,data:[]}})}));
vi.mock("@/hooks/use-task-controller",()=>({useTaskController:()=>({start:m.start})}));
vi.mock("sonner",()=>({toast:{success:m.success,error:m.error}}));
vi.mock("@/lib/queries/media-models",()=>({
 useVideoModels:()=>({data:{ok:true,data:m.videoModels}}),
 useMediaDefaults:()=>({data:{ok:true,data:m.mediaDefaults}}),
 useUpdateMediaDefaults:()=>({isPending:false,mutateAsync:m.updateDefaults}),
 availableVideoModels:(catalog:any[])=>catalog.filter((item)=>item.available),
 resolveVideoModel:(saved:string|undefined,catalog:any[])=>catalog.find((item)=>item.available&&item.id===saved)??catalog.find((item)=>item.available),
 resolveVideoMode:(saved:string,item:any)=>item.supported_modes.includes(saved)?saved:item.default_mode,
}));
vi.mock("@/lib/queries/projects",()=>({useUpdateProject:()=>({isPending:false,mutateAsync:m.updateProject})}));
vi.mock("@/lib/queries/styles",()=>({useStyles:()=>({data:{ok:true,data:[]}})}));
vi.mock("@/components/episode/narrative-workbench/group-pipeline",()=>({GroupPipeline:({onAction}:any)=><><button onClick={()=>onAction("render","generate")}>生成</button><button onClick={()=>onAction("render","regenerate")}>重生成</button><button onClick={()=>onAction("render","split")}>切分</button></>}));
vi.mock("@/components/episode/narrative-workbench/group-reference-dialog",()=>({GroupReferenceDialog:({open,onSubmit,onOpenChange}:any)=>open?<div role="dialog"><button onClick={()=>onSubmit(m.dialogSelection)}>确认</button><button onClick={()=>onOpenChange(false)}>取消</button></div>:null}));
vi.mock("@/components/episode/narrative-workbench/group-video-stage",()=>({
 GroupVideoStage:(props:any)=>{m.stageProps(props);return <><span>stage-model:{props.modelId}</span><span>stage-mode:{props.mode}</span>{props.reference?.required?<button onClick={props.reference.onManage}>管理参考图</button>:null}<button disabled={props.reference?.required&&(!props.reference.valid||props.reference.dirty||props.reference.loading||props.reference.error)} onClick={()=>props.onGenerate({video_model:props.modelId,h3_mode:props.mode==="auto"?"i2va":props.mode})}>生成组合视频</button></>},
 groupFrameSummary:()=>({allHaveFirst:true,allHaveLast:false}),
}));
vi.mock("@/components/episode/narrative-workbench/group-video-reference-dialog",()=>({GroupVideoReferenceDialog:({open,onSaved,onDirtyChange}:any)=>open?<div role="dialog" aria-label="管理视频参考图"><button onClick={()=>onDirtyChange(true)}>修改描述</button><button onClick={()=>onSaved({revision:8,max_images:5,candidates:[],selected:[{reference_id:"hero",subject_description:"Hero"}],warnings:[]})}>保存参考图</button></div>:null}));
vi.mock("@/components/episode/narrative-workbench/narrative-group-list",()=>({NarrativeGroupList:()=>null}));
vi.mock("@/stores/aspect-ratio-store",()=>({useProjectAspectRatio:()=>({orientation:m.orientation,spec:{},setOrientation:m.setOrientation})}));

describe("NarrativeGroupWorkbench references",()=>{
 beforeAll(async()=>{
  if(!i18n.isInitialized) await i18n.init({lng:"zh",fallbackLng:"zh",resources:{en:{translation:enTranslation},zh:{translation:zhTranslation}}});
  i18n.addResourceBundle("en","translation",enTranslation,true,true);
  i18n.addResourceBundle("zh","translation",zhTranslation,true,true);
 });
 beforeEach(()=>{
  vi.clearAllMocks();
  m.groups=[group];
  m.groupsLoading=false;
  m.orientation="landscape";
  m.dialogSelection={useStyle:true,selectedCharacterReferenceIds:["c1"],selectedSceneReferenceIds:[],imageSize:"1K"};
  m.mutate.mockResolvedValue({scope:"x"});
  m.generateVideo.mockResolvedValue({scope:"video-x"});
  m.generateVideoSegment.mockResolvedValue({scope:"segment-x"});
  m.updateVideoSettings.mockResolvedValue({ok:true});
  m.groupsRefetch.mockResolvedValue({data:{ok:true,data:m.groups}});
  m.promptsQuery.mockReturnValue({data:{ok:true,data:{units:[]}},isLoading:false,isError:false});
  m.referencePreviewQuery.mockReturnValue({data:undefined,isLoading:false,isFetching:false,isError:false,error:null,refetch:vi.fn()});
  m.updateDefaults.mockResolvedValue({ok:true});
  m.updateProject.mockResolvedValue({ok:true});
  m.setOrientation.mockImplementation((next: "portrait" | "landscape")=>{m.orientation=next;});
 });
 it("passes the real route identifiers to prompt review only after opening",async()=>{
  const user=userEvent.setup();
  m.groups=[{...group,stages:{...group.stages,video:{status:"completed",revision:1,manifest_asset:"/media/group.manifest.json"}}}];
  render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);
  expect(m.promptsQuery).toHaveBeenCalledWith("p",1,"g1",false);
  expect(m.promptsQuery).not.toHaveBeenCalledWith("p",1,"g1",true);
  await user.click(screen.getByRole("button",{name:"生成提示词"}));
  expect(m.promptsQuery).toHaveBeenCalledWith("p",1,"g1",true);
  expect(screen.getByRole("dialog",{name:"视频生成提示词"})).toBeInTheDocument();
 });
 it.each(["生成","重生成"])("confirms references before %s",async(label)=>{
  render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>); fireEvent.click(screen.getByText(label));
  expect(screen.getByRole("dialog")).toBeInTheDocument(); expect(m.mutate).not.toHaveBeenCalled(); fireEvent.click(screen.getByText("确认"));
  await waitFor(()=>expect(m.mutate).toHaveBeenCalledWith({groupId:"g1",stage:"render",action:label==="生成"?"generate":"regenerate",aspectRatio:"16:9",selection:{useStyle:true,selectedCharacterReferenceIds:["c1"],selectedSceneReferenceIds:[],imageSize:"1K"}}));
  expect(m.start).toHaveBeenCalledWith({scope:"x"});
  expect(m.success).toHaveBeenCalledWith("任务已进入队列");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
 });
 it("persists render model and resolution when requested",async()=>{
  m.dialogSelection={useStyle:true,selectedCharacterReferenceIds:[],selectedSceneReferenceIds:[],providerId:"grsai-main",model:"gpt-image-2-vip",imageSize:"4K",saveAsProjectDefault:true};
  render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);
  fireEvent.click(screen.getByText("生成"));
  fireEvent.click(screen.getByText("确认"));
  await waitFor(()=>expect(m.updateDefaults).toHaveBeenCalledWith(expect.objectContaining({
   narrativeRenderProvider:"grsai-main",
   narrativeRenderModel:"gpt-image-2-vip",
   narrativeRenderImageSize:"4K",
  })));
 });
 it("edits the project render model and resolution from the workbench",async()=>{
  render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);
  fireEvent.click(screen.getByRole("button",{name:"实图设置"}));
  fireEvent.change(screen.getByRole("combobox",{name:"项目实图模型"}),{target:{value:"gpt-image-2-vip"}});
  fireEvent.change(screen.getByRole("combobox",{name:"项目实图分辨率"}),{target:{value:"4K"}});
  fireEvent.click(screen.getByRole("button",{name:"保存实图设置"}));
  await waitFor(()=>expect(m.updateDefaults).toHaveBeenCalledWith(expect.objectContaining({
   narrativeRenderModel:"gpt-image-2-vip",
   narrativeRenderImageSize:"4K",
  })));
 });
 it("shows requested and actual render resolution evidence",()=>{
  m.groups=[{...group,stages:{...group.stages,render:{...group.stages.render,requested_image_size:"4K",requested_pixel_size:"3840x2160",actual_pixel_size:"3840x2160"}}}];
  render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);
  expect(screen.getByText("请求 4K / 3840x2160 · 实际 3840x2160")).toBeInTheDocument();
 });
 it("runs split directly and cancel does not submit",async()=>{
  render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>); fireEvent.click(screen.getByText("生成")); fireEvent.click(screen.getByText("取消")); expect(m.mutate).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("切分")); await waitFor(()=>expect(m.mutate).toHaveBeenCalledWith({groupId:"g1",stage:"render",action:"split",aspectRatio:"16:9"})); expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
 });
 it("clears the pending confirmation when the active group changes",()=>{
  const { rerender } = render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);
  fireEvent.click(screen.getByText("生成"));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  m.groups=[group2];
  rerender(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(m.mutate).not.toHaveBeenCalled();
 });
 it("saves the selected project aspect and uses it for later split",async()=>{
  const view=render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);
  fireEvent.click(screen.getByRole("button",{name:"9:16"}));
  await waitFor(()=>expect(m.updateProject).toHaveBeenCalledWith({aspect_ratio:"2:3"}));
  expect(m.setOrientation).toHaveBeenCalledWith("portrait");

  view.rerender(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);
  expect(screen.getByRole("button",{name:"9:16"})).toHaveAttribute("aria-pressed","true");
  fireEvent.click(screen.getByText("切分"));
  await waitFor(()=>expect(m.mutate).toHaveBeenCalledWith({groupId:"g1",stage:"render",action:"split",aspectRatio:"9:16"}));
 });
 it("rolls back the optimistic aspect when project persistence fails",async()=>{
  m.updateProject.mockRejectedValueOnce(new Error("save failed"));
  const view=render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);
  fireEvent.click(screen.getByRole("button",{name:"9:16"}));
  await waitFor(()=>expect(m.setOrientation.mock.calls).toEqual([["portrait"],["landscape"]]));
  expect(m.error).toHaveBeenCalledWith("save failed");
  view.rerender(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);
  expect(screen.getByRole("button",{name:"16:9"})).toHaveAttribute("aria-pressed","true");
 });
 it.each([
  {name:"loading",loading:true,groups:[group]},
  {name:"empty",loading:false,groups:[]},
 ])("keeps the aspect selector visible in the $name state",({loading,groups})=>{
  m.groupsLoading=loading;
  m.groups=groups;
  render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);
  expect(screen.getByRole("group",{name:"目标画幅"})).toBeInTheDocument();
 });
 it("uses the current landscape aspect for generate, regenerate, split, and group video",async()=>{
  render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);

  for(const actionLabel of ["生成","重生成"]){
   fireEvent.click(screen.getByText(actionLabel));
   fireEvent.click(screen.getByText("确认"));
   await waitFor(()=>expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  }
  fireEvent.click(screen.getByText("切分"));
  fireEvent.click(screen.getByText("生成组合视频"));

  await waitFor(()=>expect(m.generateVideo).toHaveBeenCalledWith({groupId:"g1",model:"runninghub:minimax-h3",mode:"i2va",aspectRatio:"16:9",revision:0}));
  await waitFor(()=>expect(m.mutate).toHaveBeenCalledTimes(3));
  expect(m.mutate.mock.calls.map(([request])=>request.aspectRatio)).toEqual(["16:9","16:9","16:9"]);
 });
 it("ignores a saved newapi default and exposes RunningHub MiniMax H3 as the only video model",()=>{
  render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);
  expect(screen.getByText("RunningHub MiniMax H3")).toBeInTheDocument();
  expect(screen.getByText("stage-model:runninghub:minimax-h3")).toBeInTheDocument();
  expect(screen.queryByText(/newapi/i)).not.toBeInTheDocument();
 expect(screen.queryByText("项目默认视频模型")).not.toBeInTheDocument();
  expect(screen.queryByRole("combobox",{name:"视频模型"})).not.toBeInTheDocument();
 });
 it("shows a video model combobox for multiple available workflows and uses the selection",async()=>{
  const user=userEvent.setup();
  m.videoModels=[
   {id:"runninghub:minimax-h3",label:"RunningHub MiniMax H3",provider:"runninghub",available:true,supported_modes:["auto","i2va","fl2va"],default_mode:"auto"},
   {id:"runninghub:future",label:"RunningHub Future",provider:"runninghub",available:true,supported_modes:["i2va"],default_mode:"i2va"},
  ];
  render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);

  await user.click(screen.getByRole("combobox",{name:"视频模型"}));
  await user.click(await screen.findByRole("option",{name:"RunningHub Future"}));

  await waitFor(()=>expect(m.updateDefaults).toHaveBeenCalledWith(expect.objectContaining({videoModel:"runninghub:future",videoMode:"i2va"})));
  expect(screen.getByText("stage-mode:i2va")).toBeInTheDocument();
  fireEvent.click(screen.getByText("生成组合视频"));
  await waitFor(()=>expect(m.generateVideo).toHaveBeenCalledWith(expect.objectContaining({model:"runninghub:future"})));
 });
 it("enables the preview only for a required-policy model and sends its saved revision",async()=>{
  m.mediaDefaults={...m.mediaDefaults,video_model:"runninghub:minimax-h3-ref"};
  m.videoModels=[
   {id:"runninghub:minimax-h3",label:"RunningHub MiniMax H3",provider:"runninghub",available:true,supported_modes:["auto"],default_mode:"auto"},
   {id:"runninghub:minimax-h3-ref",label:"RunningHub MiniMax H3 Ref",provider:"runninghub",available:true,supported_modes:["auto"],default_mode:"auto",reference_policy:{required:true,min_images:1,max_images:5,source_kinds:["character_identity"]}},
  ];
  m.referencePreviewQuery.mockReturnValue({data:{ok:true,data:{revision:7,max_images:5,candidates:[],selected:[{reference_id:"hero",subject_description:"Hero"}],warnings:[]}},isLoading:false,isError:false,error:null,refetch:vi.fn()});
  render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);
  expect(m.referencePreviewQuery).toHaveBeenCalledWith("p",1,"g1",true);
  fireEvent.click(screen.getByRole("button",{name:"生成组合视频"}));
  await waitFor(()=>expect(m.generateVideo).toHaveBeenCalledWith(expect.objectContaining({referenceRevision:7})));
 });
 it("synchronizes existing group settings when switching to Ref before allowing generation",async()=>{
  const user=userEvent.setup();
  m.mediaDefaults={...m.mediaDefaults,video_model:"runninghub:minimax-h3"};
  m.videoModels=[
   {id:"runninghub:minimax-h3",label:"Legacy",provider:"runninghub",available:true,supported_modes:["auto"],default_mode:"auto"},
   {id:"runninghub:minimax-h3-ref",label:"Ref",provider:"runninghub",available:true,supported_modes:["auto"],default_mode:"auto",reference_policy:{required:true,min_images:1,max_images:5,source_kinds:["character_identity"]}},
  ];
  m.groups=[{...group,video_inputs:[{beat_id:"1",has_first_frame:true,has_last_frame:false}],video_plan:{revision:3,source:"recommended",units:[],total_duration_seconds:0},video_settings:{revision:4,workflow_id:"runninghub:minimax-h3",overrides:{resolution:"720p"}},video_reference_settings:{revision:7,references:[{reference_id:"hero",subject_description:"Hero"}]}}];
  m.referencePreviewQuery.mockReturnValue({data:{ok:true,data:{revision:7,max_images:5,candidates:[],selected:[{reference_id:"hero",subject_description:"Hero"}],warnings:[]}},isLoading:false,isFetching:false,isError:false,error:null,refetch:vi.fn()});
  m.updateVideoSettings.mockImplementation(async()=>{
   m.groups=[{...m.groups[0],video_settings:{revision:5,workflow_id:"runninghub:minimax-h3-ref",overrides:{}}}];
   return {ok:true,data:m.groups[0]};
  });
  const view=render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);
  await user.click(screen.getByRole("combobox",{name:"视频模型"}));
  await user.click(await screen.findByRole("option",{name:"Ref"}));
  await waitFor(()=>expect(m.updateVideoSettings).toHaveBeenCalledWith("p",1,{groupId:"g1",expectedRevision:4,workflowId:"runninghub:minimax-h3-ref",overrides:{}}));
  view.rerender(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);
  fireEvent.click(screen.getByRole("button",{name:"生成组合视频"}));
  await waitFor(()=>expect(m.generateVideo).toHaveBeenCalledWith(expect.objectContaining({model:"runninghub:minimax-h3-ref",planRevision:3,settingsRevision:5,referenceRevision:7})));
 });
 it("blocks stale reference previews, refreshes them, and uses preview max",async()=>{
  const previewRefetch=vi.fn().mockResolvedValue({data:{ok:true,data:{revision:8,max_images:3,candidates:[],selected:[],warnings:[]}}});
  m.mediaDefaults={...m.mediaDefaults,video_model:"runninghub:minimax-h3-ref"};
  m.videoModels=[{id:"runninghub:minimax-h3-ref",label:"Ref",provider:"runninghub",available:true,supported_modes:["auto"],default_mode:"auto",reference_policy:{required:true,min_images:1,max_images:5,source_kinds:["character_identity"]}}];
  m.groups=[{...group,video_reference_settings:{revision:8,references:[]},video_segments:[{id:"seg1",group_id:"g1",shot_ids:["s1"],duration_seconds:5,continuity_reason:"",audio_mode:"project_default",style_snapshot_id:"s",status:"failed"}]}];
  m.referencePreviewQuery.mockReturnValue({data:{ok:true,data:{revision:7,max_images:3,candidates:[],selected:[{reference_id:"hero",subject_description:"Hero"}],warnings:[]}},isLoading:false,isFetching:false,isError:false,error:null,refetch:previewRefetch});
  render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);
  await waitFor(()=>expect(previewRefetch).toHaveBeenCalled());
  const props=m.stageProps.mock.calls[m.stageProps.mock.calls.length-1]?.[0];
  expect(props.reference).toMatchObject({max:3,valid:false});
  expect(screen.queryByRole("button",{name:"重试片段 seg1"})).not.toBeInTheDocument();
  await props.onGenerate({video_model:"runninghub:minimax-h3-ref",h3_mode:"auto"});
  expect(m.generateVideo).not.toHaveBeenCalled();
 });
 it("retries a segment with the complete synchronized Ref request",async()=>{
  m.mediaDefaults={...m.mediaDefaults,video_model:"runninghub:minimax-h3-ref"};
  m.videoModels=[{id:"runninghub:minimax-h3-ref",label:"Ref",provider:"runninghub",available:true,supported_modes:["auto"],default_mode:"auto",reference_policy:{required:true,min_images:1,max_images:5,source_kinds:["character_identity"]}}];
  m.groups=[{...group,stages:{...group.stages,video:{status:"failed",revision:6}},video_plan:{revision:3,source:"recommended",units:[],total_duration_seconds:0},video_settings:{revision:5,workflow_id:"runninghub:minimax-h3-ref",overrides:{}},video_reference_settings:{revision:7,references:[]},video_segments:[{id:"seg1",group_id:"g1",shot_ids:["s1"],duration_seconds:5,continuity_reason:"",audio_mode:"project_default",style_snapshot_id:"s",status:"failed"}]}];
  m.referencePreviewQuery.mockReturnValue({data:{ok:true,data:{revision:7,max_images:5,candidates:[],selected:[{reference_id:"hero",subject_description:"Hero"}],warnings:[]}},isLoading:false,isFetching:false,isError:false,error:null,refetch:vi.fn()});
  render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);
  fireEvent.click(screen.getByRole("button",{name:"重试片段 seg1"}));
  await waitFor(()=>expect(m.generateVideoSegment).toHaveBeenCalledWith({groupId:"g1",segmentId:"seg1",model:"runninghub:minimax-h3-ref",mode:"auto",revision:6,planRevision:3,settingsRevision:5,referenceRevision:7,aspectRatio:"16:9"}));
 });
 it.each([
  {name:"matching revision",model:"runninghub:minimax-h3-ref",current:7,generated:7,manifest:true,stale:false},
  {name:"changed revision",model:"runninghub:minimax-h3-ref",current:8,generated:7,manifest:true,stale:true},
  {name:"missing manifest",model:"runninghub:minimax-h3-ref",current:8,generated:7,manifest:false,stale:false},
  {name:"legacy model",model:"runninghub:minimax-h3",current:8,generated:7,manifest:true,stale:false},
 ])("reports stale reference videos only for $name",({model,current,generated,manifest,stale})=>{
  m.mediaDefaults={...m.mediaDefaults,video_model:model};
  m.videoModels=[
   {id:"runninghub:minimax-h3",label:"Legacy",provider:"runninghub",available:true,supported_modes:["auto"],default_mode:"auto"},
   {id:"runninghub:minimax-h3-ref",label:"Ref",provider:"runninghub",available:true,supported_modes:["auto"],default_mode:"auto",reference_policy:{required:true,min_images:1,max_images:5,source_kinds:["character_identity"]}},
  ];
  m.groups=[{...group,stages:{...group.stages,video:{status:"completed",revision:6,video_asset:"/video.mp4",...(manifest?{manifest_asset:"/manifest.json"}:{})}},video_reference_settings:{revision:current,references:[]}}];
  m.promptsQuery.mockReturnValue({data:{ok:true,data:{reference_settings_revision:generated,units:[]}},isLoading:false,isFetching:false,isError:false});
  render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);
  expect(screen.queryByText("参考图设置已变化，当前旧视频仍可预览；请重新生成组合视频。")).toBe(stale?screen.getByText("参考图设置已变化，当前旧视频仍可预览；请重新生成组合视频。"):null);
 });
 it.each([{isLoading:true,isFetching:true,isError:false},{isLoading:false,isFetching:false,isError:true}])("does not report stale references while the manifest query is unavailable",(queryState)=>{
  m.mediaDefaults={...m.mediaDefaults,video_model:"runninghub:minimax-h3-ref"};
  m.videoModels=[{id:"runninghub:minimax-h3-ref",label:"Ref",provider:"runninghub",available:true,supported_modes:["auto"],default_mode:"auto",reference_policy:{required:true,min_images:1,max_images:5,source_kinds:["character_identity"]}}];
  m.groups=[{...group,stages:{...group.stages,video:{status:"completed",revision:6,video_asset:"/video.mp4",manifest_asset:"/manifest.json"}},video_reference_settings:{revision:8,references:[]}}];
  m.promptsQuery.mockReturnValue({data:{ok:true,data:{reference_settings_revision:7,units:[]}},...queryState});
  render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);
  expect(screen.queryByText("参考图设置已变化，当前旧视频仍可预览；请重新生成组合视频。")).not.toBeInTheDocument();
 });
 it("keeps required-reference generation disabled while invalid or dirty",()=>{
  m.mediaDefaults={...m.mediaDefaults,video_model:"runninghub:minimax-h3-ref"};
  m.videoModels=[{id:"runninghub:minimax-h3-ref",label:"Ref",provider:"runninghub",available:true,supported_modes:["auto"],default_mode:"auto",reference_policy:{required:true,min_images:1,max_images:5,source_kinds:["character_identity"]}}];
  m.referencePreviewQuery.mockReturnValue({data:{ok:true,data:{revision:0,max_images:5,candidates:[],selected:[],warnings:[]}},isLoading:false,isError:false,error:null,refetch:vi.fn()});
  render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);
  expect(screen.getByRole("button",{name:"生成组合视频"})).toBeDisabled();
  fireEvent.click(screen.getByRole("button",{name:"管理参考图"}));
  fireEvent.click(screen.getByRole("button",{name:"修改描述"}));
  expect(screen.getByRole("button",{name:"生成组合视频"})).toBeDisabled();
 });
 it("blocks the generation callback during cached background refresh",async()=>{
  m.mediaDefaults={...m.mediaDefaults,video_model:"runninghub:minimax-h3-ref"};
  m.videoModels=[{id:"runninghub:minimax-h3-ref",label:"Ref",provider:"runninghub",available:true,supported_modes:["auto"],default_mode:"auto",reference_policy:{required:true,min_images:1,max_images:5,source_kinds:["character_identity"]}}];
  m.referencePreviewQuery.mockReturnValue({data:{ok:true,data:{revision:7,max_images:5,candidates:[],selected:[{reference_id:"hero",subject_description:"Hero"}],warnings:[]}},isLoading:false,isFetching:true,isError:false,error:null,refetch:vi.fn()});
  render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);
  const props=m.stageProps.mock.calls[m.stageProps.mock.calls.length-1]?.[0];
  expect(props.reference.loading).toBe(true);
  await props.onGenerate({video_model:"runninghub:minimax-h3-ref",h3_mode:"auto"});
  expect(m.generateVideo).not.toHaveBeenCalled();
 });
 it("rejects ok:false after a cached preview and direct callback requests for a different model",async()=>{
  m.mediaDefaults={...m.mediaDefaults,video_model:"runninghub:minimax-h3-ref"};
  m.videoModels=[{id:"runninghub:minimax-h3-ref",label:"Ref",provider:"runninghub",available:true,supported_modes:["auto"],default_mode:"auto",reference_policy:{required:true,min_images:1,max_images:5,source_kinds:["character_identity"]}}];
  const valid={ok:true,data:{revision:7,max_images:5,candidates:[],selected:[{reference_id:"hero",subject_description:"Hero"}],warnings:[]}};
  m.referencePreviewQuery.mockReturnValue({data:valid,isLoading:false,isFetching:false,isError:false,error:null,refetch:vi.fn()});
  const view=render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);
  m.referencePreviewQuery.mockReturnValue({data:{ok:false,error:"failed"},isLoading:false,isFetching:false,isError:false,error:null,refetch:vi.fn()});
  view.rerender(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);
  const props=m.stageProps.mock.calls[m.stageProps.mock.calls.length-1]?.[0];
  await props.onGenerate({video_model:"runninghub:minimax-h3-ref",h3_mode:"auto"});
  await props.onGenerate({video_model:"runninghub:minimax-h3",h3_mode:"auto"});
  expect(m.generateVideo).not.toHaveBeenCalled();
 });
});
