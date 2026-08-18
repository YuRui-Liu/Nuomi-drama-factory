import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NarrativeGroupWorkbench } from "@/components/episode/narrative-workbench/narrative-group-workbench";

const m = vi.hoisted(() => ({ mutate: vi.fn(), start: vi.fn(), refetch: vi.fn(), success: vi.fn(), groups: [] as any[] }));
const group = { id:"g1", ordinal:1, title:"G", beat_ids:["1"], layout:{rows:1,columns:1,capacity:1}, stages:{sketch:{status:"pending",revision:0},render:{status:"partial_failure",revision:0},video:{status:"pending",revision:0}}, cell_to_beat:[],errors:[],video_inputs:[] };
const group2 = { ...group, id: "g2", ordinal: 2, title: "G2" };
vi.mock("@/lib/queries/narrative-groups",()=>({
 useNarrativeGroups:()=>({data:{ok:true,data:m.groups},isLoading:false,refetch:vi.fn()}),
 useNarrativeGroupAction:()=>({mutateAsync:m.mutate,isPending:false}),
 useNarrativeGroupReferences:()=>({data:{ok:true,data:{style:{id:"s",label:"动漫",prompt:"anime",enabled_by_default:true},character_references:[],scene_references:[],limits:{max_images:9,selected_images:0,omitted_reference_ids:[]},warnings:[]}},isLoading:false,error:null,refetch:m.refetch}),
}));
vi.mock("@/hooks/use-task-controller",()=>({useTaskController:()=>({start:m.start})}));
vi.mock("sonner",()=>({toast:{success:m.success,error:vi.fn()}}));
vi.mock("@/lib/queries/media-models",()=>({useVideoModels:()=>({}),useVideoBackends:()=>({}),useMediaDefaults:()=>({}),useUpdateMediaDefaults:()=>({isPending:false,mutateAsync:vi.fn()}),mergeVideoModelCatalog:()=>[]}));
vi.mock("@/lib/queries/video",()=>({useVideoBackends:()=>({}),useRegenerateBeatVideo:()=>({mutateAsync:vi.fn()})}));
vi.mock("@/components/episode/narrative-workbench/group-pipeline",()=>({GroupPipeline:({onAction}:any)=><><button onClick={()=>onAction("render","generate")}>生成</button><button onClick={()=>onAction("render","regenerate")}>重生成</button><button onClick={()=>onAction("render","split")}>切分</button></>}));
vi.mock("@/components/episode/narrative-workbench/group-reference-dialog",()=>({GroupReferenceDialog:({open,onSubmit,onOpenChange}:any)=>open?<div role="dialog"><button onClick={()=>onSubmit({useStyle:true,selectedCharacterReferenceIds:["c1"],selectedSceneReferenceIds:[]})}>确认</button><button onClick={()=>onOpenChange(false)}>取消</button></div>:null}));
vi.mock("@/components/episode/narrative-workbench/group-video-stage",()=>({GroupVideoStage:()=>null,groupFrameSummary:()=>({allHaveFirst:false,allHaveLast:false})}));
vi.mock("@/components/episode/narrative-workbench/narrative-group-list",()=>({NarrativeGroupList:()=>null}));
vi.mock("@/components/episode/narrative-workbench/project-video-model-select",()=>({ProjectVideoModelSelect:()=>null}));

describe("NarrativeGroupWorkbench references",()=>{
 beforeEach(()=>{vi.clearAllMocks();m.groups=[group];m.mutate.mockResolvedValue({scope:"x"});});
 it.each(["生成","重生成"])("confirms references before %s",async(label)=>{
  render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>); fireEvent.click(screen.getByText(label));
  expect(screen.getByRole("dialog")).toBeInTheDocument(); expect(m.mutate).not.toHaveBeenCalled(); fireEvent.click(screen.getByText("确认"));
  await waitFor(()=>expect(m.mutate).toHaveBeenCalledWith({groupId:"g1",stage:"render",action:label==="生成"?"generate":"regenerate",selection:{useStyle:true,selectedCharacterReferenceIds:["c1"],selectedSceneReferenceIds:[]}}));
  expect(m.start).toHaveBeenCalledWith({scope:"x"});
  expect(m.success).toHaveBeenCalledWith("任务已进入队列");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
 });
 it("runs split directly and cancel does not submit",async()=>{
  render(<NarrativeGroupWorkbench project="p" episode={1} onRepairBeat={vi.fn()}/>); fireEvent.click(screen.getByText("生成")); fireEvent.click(screen.getByText("取消")); expect(m.mutate).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("切分")); await waitFor(()=>expect(m.mutate).toHaveBeenCalledWith({groupId:"g1",stage:"render",action:"split"})); expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
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
});
