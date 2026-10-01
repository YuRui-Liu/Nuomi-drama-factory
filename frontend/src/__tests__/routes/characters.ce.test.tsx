// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { ComponentType, ReactNode } from "react";

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import i18next from "i18next";
import { I18nextProvider, initReactI18next } from "react-i18next";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const runtimeState = vi.hoisted(() => ({ isCeRuntime: true }));
const toastErrorMock = vi.hoisted(() => vi.fn());
const mutation = vi.hoisted(() => () => ({ mutateAsync: vi.fn(), isPending: false }));
const buildCharactersMutationMock = vi.hoisted(() => vi.fn());
const taskStreamOptionsMock = vi.hoisted(() => vi.fn());
const extractionLockMutationMock = vi.hoisted(() => vi.fn());
const visualWorkspaceMutationMock = vi.hoisted(() => vi.fn());
const confirmVisualBibleMutationMock = vi.hoisted(() => vi.fn());
const generatePortraitMutationMock = vi.hoisted(() => vi.fn());
const createCharacterMock = vi.hoisted(() => vi.fn());
const createIdentityMock = vi.hoisted(() => vi.fn());
const updateIdentityMock = vi.hoisted(() => vi.fn());
const characterVisualState = vi.hoisted(() => ({
  extractionLocked: false,
  selectedProposalId: null as string | null,
  visualBibleStatus: undefined as
    | "draft"
    | "confirmed"
    | "superseded"
    | undefined,
}));

vi.mock("@/lib/queries/character-casting", () => ({
  useCharacterCasting: () => ({ data: { ok: true, data: { revision: null, current: null } } }),
}));
vi.mock("@/components/assets/character-casting-panel", () => ({
  CharacterCastingPanel: ({ identityId }: { identityId?: string | null }) => <div>剧情驱动选角<span data-testid="casting-stage">{identityId ?? "base"}</span></div>,
}));

vi.mock("@/lib/runtime-config", () => ({
  isCeRuntime: () => runtimeState.isCeRuntime,
}));

vi.mock("sonner", () => ({
  toast: {
    error: toastErrorMock,
    success: vi.fn(),
  },
}));

vi.mock("@tanstack/react-router", () => ({
  createLazyFileRoute: () => (options: { component: ComponentType }) => ({
    options,
    useParams: () => ({ project: "demo" }),
  }),
}));

vi.mock("@/components/episode/task-controller-provider", () => ({
  TaskControllerProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

vi.mock("@/hooks/use-task-controller", () => ({
  useTaskController: () => ({
    started: false,
    stream: {
      status: "idle",
      progress: 0,
      currentTask: "",
      result: null,
      error: null,
    },
    logs: [],
    start: vi.fn(),
    stop: vi.fn(),
    stopping: false,
  }),
}));

vi.mock("@/hooks/use-task-stream", () => ({
  useTaskStream: (options: unknown) => {
    taskStreamOptionsMock(options);
    return {
      status: "idle",
      progress: 0,
      currentTask: "",
      result: null,
      error: null,
    };
  },
}));

vi.mock("@/hooks/use-media-query", () => ({
  useMediaQuery: () => true,
}));

vi.mock("@/hooks/use-assets-deep-link", () => ({
  useAssetsDeepLink: () => ({ type: null, id: null, select: vi.fn() }),
}));

vi.mock("@/lib/queries/projects", () => ({
  useProject: () => ({
    data: {
      ok: true,
      data: {
        visual_style: "ink",
        spine_template: "drama",
        narration_style: "third_person",
      },
    },
  }),
  useUpdateProject: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("@/lib/queries/character-image-selection", () => ({
  useAssetImageSourceSelection: () => ({
    data: {
      ok: true,
      data: {
        asset_kind: "character",
        image_source_selection: "newapi_gpt_image2",
        options: { newapi_gpt_image2: "LingShan-G2" },
      },
    },
    isLoading: false,
    isFetching: false,
  }),
  useCharacterImageSelection: () => ({
    data: { ok: true, data: { character_image_selection: "seedream" } },
  }),
  useUpdateAssetImageSourceSelection: mutation,
  useUpdateCharacterImageSelection: mutation,
  useCharacterImageUsage: () => ({ data: { ok: true, data: {} } }),
}));

vi.mock("@/lib/queries/generation-credit-cost", () => ({
  useGenerationCreditCost: () => ({
    data: { ok: true, data: { display: "12 credits", cost: 12 } },
  }),
}));

vi.mock("@/lib/queries/production-assets", () => ({
  useProductionAssetSlot: () => ({ data: undefined, isLoading: false }),
  useAdoptProductionAssetVersion: mutation,
  useDeleteProductionAssetVersion: mutation,
}));

vi.mock("@/lib/queries/asset-references", () => ({
  useAssetReferences: () => ({
    referencesFor: () => [],
    coOccurrenceForScene: () => ({ identities: [], props: [] }),
    isLoading: false,
  }),
}));

vi.mock("@/lib/queries/characters", () => ({
  useCharacterVisualWorkspace: () => ({
    data: {
      ok: true,
      data: {
        character_id: "Li Qing",
        profile: { character_id: "Li Qing", name: "Li Qing", biography: "Lead character", facts: [] },
        design_proposals: [
          {
            proposal_id: "proposal-a",
            title: "Cold realism",
            rationale: "Keep the tired eyes",
            recommended: true,
            identity_anchors: ["left brow scar"],
            asymmetry_detail: "left mouth corner lower",
            quality_issues: [],
          },
          {
            proposal_id: "proposal-b",
            title: "Urban sharpness",
            rationale: "Emphasize authority",
            recommended: false,
            identity_anchors: ["shoulder-length hair"],
            asymmetry_detail: "",
            quality_issues: [],
          },
          {
            proposal_id: "proposal-c",
            title: "Restrained realism",
            rationale: "Keep an ordinary silhouette",
            recommended: false,
            identity_anchors: ["slim frame"],
            asymmetry_detail: "right eye smaller",
            quality_issues: [],
          },
        ],
        selected_proposal_id: characterVisualState.selectedProposalId,
        visual_bible: characterVisualState.visualBibleStatus
          ? {
              revision_id: "vb-1",
              status: characterVisualState.visualBibleStatus,
              face_shape: "narrow",
              facial_features: [],
              hair_style: "shoulder-length",
              body_type: "slim",
              distinctive_features: [],
              outfit_states: {},
            }
          : null,
        legacy_fields: [],
      },
    },
  }),
  useUpdateCharacterExtractionLock: () => ({
    mutateAsync: extractionLockMutationMock,
    isPending: false,
  }),
  useUpdateCharacterVisualWorkspace: () => ({
    mutateAsync: visualWorkspaceMutationMock,
    isPending: false,
  }),
  useConfirmCharacterVisualBible: () => ({
    mutateAsync: confirmVisualBibleMutationMock,
    isPending: false,
  }),
  useCharacters: () => ({
    isLoading: false,
    data: {
      ok: true,
      data: [
        {
          name: "Li Qing",
          aliases: [],
          role: "主角",
          gender: "男",
          age_group: "middle",
          is_main: true,
          description: "Lead character",
          face_prompt: "sharp eyes",
          body_type: "slim",
          portrait_url: "",
          extraction_locked: characterVisualState.extractionLocked,
        },
      ],
    },
  }),
  useBuildCharacters: () => ({
    mutateAsync: buildCharactersMutationMock,
    isPending: false,
  }),
  useCreateCharacter: () => ({ mutateAsync: createCharacterMock, isPending: false }),
  useUpdateCharacter: mutation,
  useDeleteCharacter: mutation,
  useCharacterAssetHistory: () => ({ data: undefined, isLoading: false }),
  useRestoreCharacterAsset: mutation,
  useCharacterIdentities: () => ({
    data: {
      ok: true,
      data: [
        {
          identity_id: "id-middle",
          identity_name: "Middle",
          appearance_details: "green robe and clean silhouette",
          face_prompt: "sharp eyes",
          age_group: "middle",
          body_type: "slim",
          image_url: "",
          portrait_image_url: "",
          costume_image_url: "",
        },
      ],
    },
  }),
  useCreateIdentity: () => ({ mutateAsync: createIdentityMock, isPending: false }),
  useUpdateIdentity: () => ({ mutateAsync: updateIdentityMock, isPending: false }),
  useDeleteIdentity: mutation,
  useDeleteIdentityImage: mutation,
  useDeleteIdentityCostume: mutation,
  useGenerateIdentityImageAsync: mutation,
  useGenerateIdentityPortraitAsync: mutation,
  useUploadIdentityImage: mutation,
  useUploadCostumeImage: mutation,
  useUploadIdentityPortrait: mutation,
  useGeneratePortraitAsync: () => ({
    mutateAsync: generatePortraitMutationMock,
    isPending: false,
  }),
  useUploadPortrait: mutation,
  useIdentityAttempts: () => ({
    data: { ok: true, data: { image_attempts: 0, portrait_attempts: 0 } },
    refetch: vi.fn(),
  }),
  useIdentityOwnerIndex: () => ({ ownerOf: () => null }),
}));

vi.mock("@/components/assets/character-voice-panel", () => ({
  CharacterVoicePanel: () => <div data-testid="character-voice-panel" />,
}));

vi.mock("@/components/assets/project-style-chip", () => ({
  ProjectStyleChip: () => <div data-testid="project-style-chip" />,
}));

vi.mock("@/components/assets/scenes-panel", () => ({
  ScenesPanel: () => <div data-testid="scenes-panel" />,
}));

vi.mock("@/components/assets/props-panel", () => ({
  PropsPanel: () => <div data-testid="props-panel" />,
}));

vi.mock("@/components/assets/narrator-voice-panel", () => ({
  NarratorVoicePanel: () => <div data-testid="narrator-voice-panel" />,
}));

import { Route } from "@/routes/_app/projects.$project/characters.lazy";

const i18n = i18next.createInstance();

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "en",
    fallbackLng: "en",
    resources: {
      en: {
        translation: {
          common: { novelImportRequired: "Please import a novel first" },
        },
      },
      zh: {
        translation: {
          common: { novelImportRequired: "请先导入小说" },
        },
      },
    },
    interpolation: { escapeValue: false },
  });
});

vi.mock("@/lib/queries/voice-acceptance", () => ({
  useVoiceAcceptance: () => ({ data: { ok: true, data: [] }, isLoading: false }),
  useReviewVoiceAcceptance: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

function renderCharactersPage() {
  const Component = Route.options.component as ComponentType;
  return render(
    <I18nextProvider i18n={i18n}>
      <Component />
    </I18nextProvider>,
  );
}

describe("characters page CE generation credit gating", () => {
  it("edits same-age identity face details and enables its portrait upload", async () => {
    renderCharactersPage();
    const face = screen.getByLabelText("身份面部设定");
    expect(face).toHaveValue("sharp eyes");
    await userEvent.clear(face);
    await userEvent.type(face, "保留原脸，左颊伤痕");
    await userEvent.click(screen.getByRole("button", { name: "保存身份面部设定" }));
    await waitFor(() => expect(updateIdentityMock).toHaveBeenCalledWith({ identityId: "id-middle", data: { face_prompt: "保留原脸，左颊伤痕" } }));
    const portrait = screen.getByText("characters.identities.portraitTitle").parentElement!;
    expect(within(portrait).getByRole("button", { name: "characters.identities.upload" })).toBeEnabled();
  });
  it("saves one reusable crowd template with editable context without generating media", async () => {
    renderCharactersPage();
    await userEvent.click(screen.getByRole("button", { name: "characters.addCharacter" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getAllByRole("textbox")[0], "街头丧尸模板");
    await userEvent.click(within(dialog).getByRole("radio", { name: "丧尸群演模板" }));
    await userEvent.type(within(dialog).getByLabelText("原文设定与外观细节"), "旧工作服，无特定眼睛颜色");
    await userEvent.click(within(dialog).getByRole("radio", { name: "独立丧尸角色" }));
    expect(within(dialog).getByLabelText("原文设定与外观细节")).toHaveValue("旧工作服，无特定眼睛颜色");
    await userEvent.click(within(dialog).getByRole("radio", { name: "丧尸群演模板" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "common.confirm" }));
    await waitFor(() => expect(createCharacterMock).toHaveBeenCalledWith(expect.objectContaining({
      name: "街头丧尸模板", extraction_locked: true,
      description: expect.stringContaining("丧尸群演模板"),
      face_prompt: expect.stringContaining("旧工作服，无特定眼睛颜色"),
    })));
    expect(createCharacterMock.mock.calls[0][0].face_prompt).toContain("单个代表人物");
    expect(generatePortraitMutationMock).not.toHaveBeenCalled();
  });

  it("clears cancelled presets and creates infected forms as separate identities", async () => {
    renderCharactersPage();
    await userEvent.click(screen.getByRole("button", { name: "characters.identities.addNew" }));
    let dialog = screen.getByRole("dialog");
    await userEvent.click(within(dialog).getByRole("radio", { name: "感染 / 丧尸形态" }));
    await userEvent.type(within(dialog).getByLabelText("原文设定与外观细节"), "伤痕在左颊");
    await userEvent.click(within(dialog).getByRole("button", { name: "common.cancel" }));
    await userEvent.click(screen.getByRole("button", { name: "characters.identities.addNew" }));
    dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("radio", { name: "普通身份" })).toBeChecked();
    expect(within(dialog).getByLabelText("原文设定与外观细节")).toHaveValue("");
    await userEvent.type(within(dialog).getByPlaceholderText("characters.identities.newNamePlaceholder"), "感染后");
    await userEvent.click(within(dialog).getByRole("radio", { name: "感染 / 丧尸形态" }));
    await userEvent.type(within(dialog).getByLabelText("原文设定与外观细节"), "伤痕在左颊");
    await userEvent.click(within(dialog).getByRole("button", { name: "common.confirm" }));
    await waitFor(() => expect(createIdentityMock).toHaveBeenCalledWith(expect.objectContaining({
      identity_name: "感染后", face_prompt: expect.stringContaining("伤痕在左颊"),
      appearance_details: expect.stringContaining("保留原角色可辨认的面部身份"),
    })));
    expect(createCharacterMock).not.toHaveBeenCalled();
  });
  it("opens the approved asset workspace on identity and keeps portrait, costume, casting, voice and history independently reachable", async () => {
    renderCharactersPage();
    expect(await screen.findByRole("tab", { name: "身份" })).toHaveAttribute("aria-selected", "true");
    for (const name of ["肖像", "服装", "剧情选角", "声音", "历史"]) {
      expect(screen.getByRole("tab", { name })).toBeInTheDocument();
    }
    await userEvent.click(screen.getByRole("tab", { name: "肖像" }));
    expect(screen.getByRole("button", { name: "前往选角" })).toBeVisible();
    await userEvent.click(screen.getByRole("tab", { name: "历史" }));
    expect(screen.getByText("按参考槽位查看与恢复，已有分镜和视频不会自动重生成。")).toBeVisible();
  });
  it("keeps role voice work in the role and moves independent samples to the voice archive", async () => {
    renderCharactersPage();
    expect(await screen.findByRole("tab", { name: "声音" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "声音验收" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看历史声音样本" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "characters.assetTabs.voices" }));
    await userEvent.click(screen.getByText("历史声音样本"));
    await userEvent.click(screen.getByRole("button", { name: "查看历史声音样本" }));
    expect(screen.getByRole("dialog", { name: "历史声音样本" })).toBeInTheDocument();
  });
  beforeEach(async () => {
    await i18n.changeLanguage("en");
    runtimeState.isCeRuntime = true;
    toastErrorMock.mockClear();
    createCharacterMock.mockReset();
    createIdentityMock.mockReset();
    updateIdentityMock.mockReset();
    buildCharactersMutationMock.mockReset();
    taskStreamOptionsMock.mockClear();
    extractionLockMutationMock.mockReset();
    extractionLockMutationMock.mockResolvedValue({ ok: true, data: {} });
    visualWorkspaceMutationMock.mockReset();
    visualWorkspaceMutationMock.mockResolvedValue({ ok: true, data: {} });
    confirmVisualBibleMutationMock.mockReset();
    confirmVisualBibleMutationMock.mockResolvedValue({ ok: true, data: {} });
    generatePortraitMutationMock.mockReset();
    generatePortraitMutationMock.mockResolvedValue({
      ok: true,
      scope: "character:Li Qing:portrait",
    });
    characterVisualState.extractionLocked = false;
    characterVisualState.selectedProposalId = null;
    characterVisualState.visualBibleStatus = undefined;
    Element.prototype.scrollTo = vi.fn();
    window.localStorage.clear();
  });

  it("hides portrait and identity generation costs and keeps credit styling out of CE dialogs", async () => {
    const user = userEvent.setup();
    renderCharactersPage();
    await user.click(await screen.findByRole("tab", { name: "肖像" }));

    expect(await screen.findAllByText("Li Qing")).not.toHaveLength(0);
    expect(
      await screen.findByRole("button", {
        name: "前往选角",
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Middle", hidden: true })).toBeInTheDocument();

    expect(screen.queryByText("12 credits")).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "身份" }));
    const identityGenerate = screen
      .getAllByRole("button", { name: "characters.identities.generate" })
      .find((button) => !button.hasAttribute("disabled"));
    expect(identityGenerate).toBeDefined();
    await user.click(identityGenerate!);

    const dialog = await screen.findByRole("alertdialog");
    const dialogAction = within(dialog).getByRole("button", {
      name: "characters.identities.generate",
    });
    await waitFor(() => expect(dialogAction.closest("[role='alertdialog']")).toBeTruthy());

    expect(dialogAction).not.toHaveClass("border-[#007A87]");
    expect(dialogAction).not.toHaveClass("hover:border-[#007A87]");
    expect(dialogAction).not.toHaveClass("dark:border-[#007A87]");
    expect(identityGenerate).not.toHaveClass("pr-9");
    expect(dialogAction).not.toHaveClass("pr-9");
    expect(screen.queryByText("12 credits")).not.toBeInTheDocument();
    expect(toastErrorMock).not.toHaveBeenCalledWith(
      expect.stringMatching(/积分不足|credit|insufficient/i),
    );
  });

  it.each([
    ["en", "Please import a novel first"],
    ["zh", "请先导入小说"],
  ])(
    "localizes the backend prerequisite error in %s without starting a missing task stream",
    async (language, expectedMessage) => {
      await i18n.changeLanguage(language);
      buildCharactersMutationMock.mockResolvedValue({
        ok: false,
        code: "NOVEL_IMPORT_REQUIRED",
        error: "请先导入小说",
      });
      const user = userEvent.setup();
      renderCharactersPage();

      await user.click(
        await screen.findByRole("button", { name: /characters\.autoExtract/ }),
      );
      const dialog = await screen.findByRole("alertdialog");
      await user.click(
        within(dialog).getByRole("button", { name: "common.confirm" }),
      );

      await waitFor(() =>
        expect(toastErrorMock).toHaveBeenCalledWith(expectedMessage),
      );
      expect(
        taskStreamOptionsMock.mock.calls.some(
          ([options]) => (options as { enabled?: boolean }).enabled === true,
        ),
      ).toBe(false);
    },
  );

  it("locks character extraction and keeps previous visual proposals read only", async () => {
    const user = userEvent.setup();
    const firstRender = renderCharactersPage();

    await user.click(await screen.findByRole("button", { name: "锁定角色" }));
    expect(extractionLockMutationMock).toHaveBeenCalledWith(true);

    await user.click(screen.getByRole("tab", { name: "剧情选角" }));
    await user.click(screen.getByText("现有设定（只读）"));
    expect(screen.getByRole("button", { name: /Urban sharpness/ })).toBeDisabled();
    expect(visualWorkspaceMutationMock).not.toHaveBeenCalled();
    expect(screen.getByText("推荐")).toBeInTheDocument();

    firstRender.unmount();
    characterVisualState.extractionLocked = true;
    renderCharactersPage();
    await user.click(await screen.findByRole("button", { name: "解锁角色" }));
    expect(extractionLockMutationMock).toHaveBeenLastCalledWith(false);
  });

  it("guides portrait actions to the casting panel", async () => {
    const user = userEvent.setup();
    renderCharactersPage();
    expect(await screen.findByRole("tab", { name: "身份" })).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("tab", { name: "肖像" }));

    const generateButton = await screen.findByRole("button", {
      name: "前往选角",
    });
    expect(generateButton).toBeEnabled();
    await user.click(generateButton);

    expect(screen.getByText("剧情驱动选角")).toBeVisible();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(generatePortraitMutationMock).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "选择推荐提案" })).not.toBeInTheDocument();
  });
  it("routes identity portrait entry with its stable ID and base entry with null", async () => {
    const user = userEvent.setup();
    renderCharactersPage();
    await user.click(await screen.findByRole("tab", { name: "身份" }));
    await user.click(screen.getByRole("button", { name: "前往选角" }));
    expect(screen.getByTestId("casting-stage")).toHaveTextContent("id-middle");
    await user.click(screen.getByRole("tab", { name: "肖像" }));
    await user.click(screen.getByRole("button", { name: "前往选角" }));
    expect(screen.getByTestId("casting-stage")).toHaveTextContent("base");
  });

  it("uses casting even when the legacy VisualBible is confirmed", async () => {
    const user = userEvent.setup();
    characterVisualState.selectedProposalId = "proposal-a";
    characterVisualState.visualBibleStatus = "confirmed";
    renderCharactersPage();
    await user.click(await screen.findByRole("tab", { name: "肖像" }));

    const generateButton = await screen.findByRole("button", {
      name: "前往选角",
    });
    expect(generateButton).toBeEnabled();
    await user.click(generateButton);

    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(generatePortraitMutationMock).not.toHaveBeenCalled();
    expect(screen.getByText("剧情驱动选角")).toBeVisible();
    await user.click(screen.getByText("现有设定（只读）"));
    expect(screen.getByText("VisualBible 已确认")).toBeInTheDocument();
  });

  it("does not expose the old VisualBible confirmation alongside casting", async () => {
    const user = userEvent.setup();
    characterVisualState.selectedProposalId = "proposal-a";
    characterVisualState.visualBibleStatus = "draft";
    renderCharactersPage();

    await user.click(await screen.findByRole("tab", { name: "剧情选角" }));
    await user.click(screen.getByText("现有设定（只读）"));
    expect(screen.queryByRole("button", { name: "确认 VisualBible" })).not.toBeInTheDocument();
    expect(confirmVisualBibleMutationMock).not.toHaveBeenCalled();
  });
});
