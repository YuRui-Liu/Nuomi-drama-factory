import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TeamUsersDialog } from "@/components/account/team-users-dialog";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/auth-store";

const navigate = vi.hoisted(() => vi.fn());
const successToast = vi.hoisted(() => vi.fn());
vi.mock("@tanstack/react-router", () => ({ useNavigate: () => navigate }));
vi.mock("sonner", () => ({ toast: { success: successToast } }));
function renderDialog(queryClient = new QueryClient()) {
  return render(<QueryClientProvider client={queryClient}><TeamUsersDialog open onOpenChange={() => {}} /></QueryClientProvider>);
}

afterEach(() => { cleanup(); useAuthStore.getState().reset(); vi.unstubAllGlobals(); vi.clearAllMocks(); });
const admin = { id: "admin", username: "owner", role: "admin", enabled: true };
const member = { id: "member", username: "alice", role: "member", enabled: true };
const response = (data: unknown) => new Response(JSON.stringify({ ok: true, data }));

it("loads accounts, protects admins, and creates a member using an explicit password", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(response([admin, member]))
    .mockResolvedValueOnce(response({ ...member, id: "bob", username: "bob" }))
    .mockResolvedValueOnce(response([admin, member, { ...member, id: "bob", username: "bob" }]));
  vi.stubGlobal("fetch", fetcher);
  renderDialog();
  await screen.findByText("alice");
  expect(screen.getByRole("button", { name: "禁用 owner" })).toBeDisabled();
  expect(screen.getByLabelText("初始密码")).toHaveValue("");
  fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "bob" } });
  fireEvent.change(screen.getByLabelText("初始密码"), { target: { value: "dummy-secure-password" } });
  fireEvent.click(screen.getByRole("button", { name: "创建成员" }));
  await screen.findByText("bob");
  expect(fetcher).toHaveBeenCalledWith("/api/v1/admin/users", expect.objectContaining({
    method: "POST", credentials: "include", body: JSON.stringify({ username: "bob", password: "dummy-secure-password" }),
  }));
  expect(screen.getByLabelText("初始密码")).toHaveValue("");
});

it("surfaces failed updates and resets passwords without exposing them in the list", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(response([member]))
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Permission denied" }), { status: 403 }))
    .mockResolvedValueOnce(response(member)).mockResolvedValueOnce(response([member]));
  vi.stubGlobal("fetch", fetcher);
  renderDialog();
  fireEvent.click(await screen.findByRole("button", { name: "禁用 alice" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Permission denied");
  fireEvent.click(screen.getByRole("button", { name: "重置 alice 的密码" }));
  fireEvent.change(screen.getByLabelText("新密码"), { target: { value: "dummy-replacement-password" } });
  fireEvent.click(screen.getByRole("button", { name: "保存新密码" }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith("/api/v1/admin/users/member", expect.objectContaining({
    method: "PATCH", body: JSON.stringify({ password: "dummy-replacement-password" }),
  })));
  await waitFor(() => expect(screen.queryByLabelText("新密码")).not.toBeInTheDocument());
});

it("clears the revoked session and navigates to login after resetting its own password", async () => {
  useAuthStore.setState({ username: "owner", role: "admin" });
  const queryClient = new QueryClient();
  queryClient.setQueryData(["projects"], ["private-project"]);
  const fetcher = vi.fn().mockResolvedValueOnce(response([admin]))
    .mockResolvedValueOnce(response(admin))
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Session revoked" }), { status: 401 }));
  vi.stubGlobal("fetch", fetcher);
  renderDialog(queryClient);
  fireEvent.click(await screen.findByRole("button", { name: "重置 owner 的密码" }));
  fireEvent.change(screen.getByLabelText("新密码"), { target: { value: "replacement-password" } });
  fireEvent.click(screen.getByRole("button", { name: "保存新密码" }));
  await waitFor(() => expect(navigate).toHaveBeenCalledWith({ to: "/login", replace: true }));
  expect(useAuthStore.getState().username).toBeNull();
  expect(queryClient.getQueryData(["projects"])).toBeUndefined();
  expect(successToast).toHaveBeenCalledWith("密码已更新，请使用新密码重新登录");
  expect(fetcher.mock.calls.filter(([url]) => url === "/api/v1/admin/users")).toHaveLength(1);
  expect(fetcher).toHaveBeenCalledWith("/api/v1/auth/logout", expect.objectContaining({ method: "POST" }));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
