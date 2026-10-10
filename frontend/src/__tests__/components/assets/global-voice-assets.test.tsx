import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/__mocks__/msw/server";
import ky from "ky";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
vi.mock("@/lib/api", () => ({ api: ky.create({ baseUrl: "http://localhost:3000/" }) }));
import { GlobalVoicePicker, VoiceAssetFavorite } from "@/components/assets/global-voice-assets";

function show(node: ReactNode) { return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>); }
const asset = { id: "a", name: "沉稳将军", description: "低沉", duration: 3, tags: { timbre: ["低沉"], identity: ["将军"], personality: ["沉稳"] } };

describe("全局配音资产", () => {
  it("collects categorized custom tags and loads original metadata on duplicate", async () => {
    let sent: unknown;
    server.use(http.post("*/api/v1/projects/p/characters/:name/voice-candidates/c/favorite", async ({ request }) => {
      sent = await request.json(); return HttpResponse.json({ ok: true, data: { asset, created: false } });
    }));
    show(<VoiceAssetFavorite project="p" character="甲" candidateId="c" />);
    fireEvent.click(screen.getByText("收藏为全局配音资产"));
    fireEvent.change(screen.getByLabelText("音色特点"), { target: { value: "低沉，沙哑，低沉" } });
    fireEvent.change(screen.getByLabelText("身份特点"), { target: { value: "将军" } });
    fireEvent.change(screen.getByLabelText("性格特点"), { target: { value: "沉稳" } });
    fireEvent.click(screen.getByText("保存"));
    await screen.findByText(/此音频已收藏/);
    expect(sent).toMatchObject({ tags: { timbre: ["低沉", "沙哑"], identity: ["将军"], personality: ["沉稳"] } });
    expect(screen.getByDisplayValue("沉稳将军")).toBeTruthy();
  });

  it("filters tags and adopts into the selected character slot", async () => {
    const queries: string[] = []; let adopted: unknown;
    server.use(
      http.get("*/api/v1/voice-assets/tags", () => HttpResponse.json({ data: asset.tags })),
      http.get("*/api/v1/voice-assets", ({ request }) => { queries.push(request.url); return HttpResponse.json({ data: { items: [asset], total: 1 } }); }),
      http.post("*/api/v1/projects/p/characters/:name/voice-assets/a/adopt", async ({ request }) => { adopted = await request.json(); return HttpResponse.json({ ok: true, data: {} }); }),
    );
    show(<GlobalVoicePicker project="p" character="甲" slot="default" reference={{ path: "", sha256: "" }} />);
    fireEvent.click(screen.getByText("从全局配音库选择"));
    fireEvent.click(await screen.findByRole("button", { name: "将军" }));
    await waitFor(() => expect(queries.some(url => new URL(url).searchParams.get("identity") === "将军")).toBe(true));
    fireEvent.click(screen.getByText("已试听，采用"));
    await waitFor(() => expect(adopted).toMatchObject({ slot: "default", expected_reference: { path: "", sha256: "" } }));
  });
});
