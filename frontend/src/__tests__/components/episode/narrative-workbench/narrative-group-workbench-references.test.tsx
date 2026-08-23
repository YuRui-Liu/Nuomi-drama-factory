import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NarrativeGroupWorkbench } from "@/components/episode/narrative-workbench/narrative-group-workbench";

const m = vi.hoisted(() => ({
 mutate: vi.fn(),
 start: vi.fn(),
 refetch: vi.fn(),
 success: vi.fn(),
 error: vi.fn(),
 generateVideo: vi.fn(),
 updateDefaults: vi.fn(),
 updateProject: vi.fn(),
 setOrientation: vi.fn(),
 orientation: "landscape" as "portrait" | "landscape",
 mediaDefaults: {video_model:"newapi_seedance-1.0-pro-fast",h3_mode:"auto",narrative_sketch_provider:"grsai-main",narrative_sketch_model:"nano-banana-2",narrative_render_provider:"grsai-main",narrative_render_model:"gpt-image-2"},
 videoModels: [{id:"runninghub:minimax-h3",label:"RunningHub MiniMax H3",provider:"runninghub",available:true,supported_modes:["auto","i2va","fl2va"],default_mode:"auto"}],
 groupsLoading: false,
 groups: [] as any[],
}));
const group = { id:"g1", ordinal:1, title:"G", beat_ids:["1"], layout:{rows:1,columns:1,capacity:1}, stages:{sketch:{status:"pending",revision:0},render:{status:"partial_failure",revision:0},video:{status:"pending",revision:0}}, cell_to_beat:[],errors:[],video_inputs:[] };
const group2 = { ...group, id: "g2", ordinal: 2, title: "G2" };
vi.mock("@/lib/queries/narrative-groups",()=>({
 useNarrativeGroups:()=>({data:{ok:true,data:m.groups},isLoading:m.groupsLoading,refetch:vi.fn()}),
 useNarrativeGroupAction:()=>({mutateAsync:m.mutate,isPending:false}),
 useNarrativeGroupReferences:()=>({data:{ok:true,data:{style:{id:"s",label:"动漫",prompt:"anime",enabled_by_default:true},character_references:[],scene_references:[],limits:{max_images:9,selected_images:0,omitted_reference_ids:[]},warnings:[]}},isLoading:false,error:null,refetch:m.refetch}),
 useGenerateNarrativeGroupVideo:()=>({mutateAsync:m.generateVideo}),
 useUpdateNarrativeGroupVideoDialogueSource:()=>({mutateAsync:vi.fn()}),
 narrativeGroupTaskScope:()=>"grid-scope",
 narrativeGroupVideoTaskScope:()=>"video-scope",
}));
vi.mock("@/hooks/use-task-controller",()=>({useTaskController:()=>({start:m.start})}));
vi.mock("sonner",()=>({toast:{success:m.success,error:m.error}}));
vi.mock("@/lib/queries/media-models",()=>({
 useVideoModels:()=>({data:{ok:true,data:m.videoModels}}),
 useMediaDefaults:()=>({data:{ok:true,data:m.mediaDefaults}}),
 useUpdateMediaDefaults:()=>({isPending:false,mutateAsync:m.updateDefaults}),
 availableVideoModels:(catalog:any[])=>catalog.filter((item)=>item.available),
 resolveVideoModel:(saved:string|undefined,catalog:any[])=>catalog.find((item)=>item.available&&item.id===saved)??catalog.find((item)=>item.available),
}));
vi.mock("@/lib/queries/projects",()=>({useUpdateProject:()=>({isPending:false,mutateAsync:m.updateProject})}));
vi.mock("@/components/episode/narrative-workbench/group-pipeline",()=>({GroupPipeline:({onAction}:any)=><><button onClick={()=>onAction("render","generate")}>生成</button><button onClick={()=>onAction("render","regenerate")}>重生成</button><button onClick={()=>onAction("render","split")}>切分</button></>}));
vi.mock("@/components/episode/narrative-workbench/group-reference-dialog",()=>({GroupReferenceDialog:({open,onSubmit,onOpenChange}:any)=>open?<div role="dialog"><button onClick={()=>onSubmit({useStyle:true,selectedCharacterReferenceIds:["c1"],selectedSceneReferenceIds:[]})}>确认</button><button onClick={()=>onOpenChange(false)}>取消</button></div>:null}));
vi.mock("@/components/episode/narrative-workbench/group-video-stage",()=>({
 GroupVideoStage:({onGenerate,modelId}:any)=><><span>stage-model:{modelId}</span><button onClick={()=>onGenerate({video_model:modelId,h3_mode:"i2va"})}>生成组合视频</button></>,
 groupFrameSummary:()=>({allHaveFirst:true,allHaveLast:false}),
}));
vi.mock("@/components/episode/narrative-workbench/narrative-group-list",()=>({NarrativeGroupList:()=>null}));
vi.mock("@/stores/aspect-ratio-store",()=>({useProjectAspectRatio:()=>({orientation:m.orientation,spec:{},setOrientation:m.setOrientation})}));

describe("NarrativeGroupWorkbench references",()=>{
 beforeEach(()=>{
  vi.clearAllMocks();
  m.groups=[group];
  m.groupsLoading=false;
  m.orientation="landscape";
  m.mutate.mockResolvedValue({scope:"x"});
  m.generateVideo.mockResolvedValue({scope:"video-x"});
  m.updateDefaults.mockResolvedValue({ok:true});
  m.updateProject.mockResolvedValue({ok:true});
  m.setOrientation.mockImplementation((next: "portrait" | "landscape")=>{m.orientation=next;});
 });
 it.each(["生成","重生成"])("confirms references before %s",async(label)=>{
  render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>); fireEvent.click(screen.getByText(label));
  expect(screen.getByRole("dialog")).toBeInTheDocument(); expect(m.mutate).not.toHaveBeenCalled(); fireEvent.click(screen.getByText("确认"));
  await waitFor(()=>expect(m.mutate).toHaveBeenCalledWith({groupId:"g1",stage:"render",action:label==="生成"?"generate":"regenerate",aspectRatio:"16:9",selection:{useStyle:true,selectedCharacterReferenceIds:["c1"],selectedSceneReferenceIds:[]}}));
  expect(m.start).toHaveBeenCalledWith({scope:"x"});
  expect(m.success).toHaveBeenCalledWith("任务已进入队列");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
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
   {id:"runninghub:future",label:"RunningHub Future",provider:"runninghub",available:true,supported_modes:["auto","i2va"],default_mode:"auto"},
  ];
  render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>);

  await user.click(screen.getByRole("combobox",{name:"视频模型"}));
  await user.click(await screen.findByRole("option",{name:"RunningHub Future"}));

  await waitFor(()=>expect(m.updateDefaults).toHaveBeenCalledWith(expect.objectContaining({videoModel:"runninghub:future"})));
  fireEvent.click(screen.getByText("生成组合视频"));
  await waitFor(()=>expect(m.generateVideo).toHaveBeenCalledWith(expect.objectContaining({model:"runninghub:future"})));
 });
});
