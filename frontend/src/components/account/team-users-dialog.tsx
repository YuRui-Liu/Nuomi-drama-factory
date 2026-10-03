import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";
import { useAuthStore } from "@/stores/auth-store";
import { resetUserSessionState } from "@/lib/reset-region-state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";

interface TeamUser { id: string; username: string; role: "admin" | "member"; enabled: boolean }

async function usersRequest<T>(path = "", body?: object, method = "GET"): Promise<T> {
  const response = await fetch(`/api/v1/admin/users${path}`, {
    method, credentials: "include", headers: { "Content-Type": "application/json" },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const result = await response.json().catch(() => null);
  if (!response.ok || result?.ok !== true) {
    const detail = result?.error || result?.detail;
    throw new Error(typeof detail === "string" ? detail : `请求失败（${response.status}）`);
  }
  return result.data;
}

export function TeamUsersDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const currentUsername = useAuthStore((state) => state.username);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [users, setUsers] = useState<TeamUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [resetUser, setResetUser] = useState<TeamUser | null>(null);
  const [newPassword, setNewPassword] = useState("");
  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoading(true); setError(""); setNotice(""); setPassword(""); setNewPassword(""); setResetUser(null);
    usersRequest<TeamUser[]>().then((data) => { if (active) setUsers(data); })
      .catch((err) => { if (active) setError(err instanceof Error ? err.message : "读取成员失败"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [open]);

  async function mutate(path: string, body: object, method: string, success: () => void, reauthenticate = false) {
    setBusy(true); setError(""); setNotice("");
    try {
      await usersRequest(path, body, method);
      success();
      if (reauthenticate) {
        // Password changes revoke this session. Do not refresh protected data.
        await useAuthStore.getState().logout();
        resetUserSessionState({ queryClient });
        onOpenChange(false);
        toast.success("密码已更新，请使用新密码重新登录");
        void navigate({ to: "/login", replace: true });
        return;
      }
      setNotice("已保存");
      setUsers(await usersRequest<TeamUser[]>());
    } catch (err) { setError(err instanceof Error ? err.message : "保存失败"); }
    finally { setBusy(false); }
  }

  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="sm:max-w-2xl max-h-[85vh] overflow-y-auto">
      <DialogHeader><DialogTitle>团队成员管理</DialogTitle>
        <DialogDescription>创建成员账号，并在项目分享中分配项目权限。管理员账号不可在此禁用。</DialogDescription>
      </DialogHeader>
      {error && <p role="alert" className="text-destructive">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <form className="grid gap-3 rounded-lg border p-4" onSubmit={(event) => {
        event.preventDefault();
        void mutate("", { username: username.trim(), password }, "POST", () => { setUsername(""); setPassword(""); });
      }}>
        <label htmlFor="team-username">用户名</label>
        <Input id="team-username" autoComplete="off" value={username} onChange={(e) => setUsername(e.target.value)} required />
        <label htmlFor="team-password">初始密码</label>
        <Input id="team-password" type="password" autoComplete="new-password" minLength={12} value={password} onChange={(e) => setPassword(e.target.value)} required />
        <p className="text-xs text-muted-foreground">设置至少 12 位密码，通过安全渠道交给成员。</p>
        <Button type="submit" disabled={busy || loading || !username.trim() || password.length < 12}>创建成员</Button>
      </form>
      {loading ? <p role="status">加载成员中…</p> : <ul className="space-y-3">
        {users.map((user) => <li key={user.id} className="flex flex-wrap items-center gap-2 rounded-lg border p-3">
          <div className="min-w-0 flex-1"><p className="break-all font-medium">{user.username}</p>
            <p className="text-xs text-muted-foreground">{user.role === "admin" ? "管理员" : "成员"} · {user.enabled ? "已启用" : "已禁用"}</p></div>
          <Button variant="outline" disabled={busy} aria-label={`重置 ${user.username} 的密码`} onClick={() => { setResetUser(user); setNewPassword(""); }}>重置密码</Button>
          <Button variant="outline" disabled={busy || user.role === "admin"} aria-label={`${user.enabled ? "禁用" : "启用"} ${user.username}`}
            onClick={() => void mutate(`/${encodeURIComponent(user.id)}`, { enabled: !user.enabled }, "PATCH", () => {})}>{user.enabled ? "禁用" : "启用"}</Button>
        </li>)}
      </ul>}
      {resetUser && <form className="grid gap-3 rounded-lg border p-4" onSubmit={(event) => {
        event.preventDefault();
        void mutate(`/${encodeURIComponent(resetUser.id)}`, { password: newPassword }, "PATCH", () => { setResetUser(null); setNewPassword(""); }, resetUser.username === currentUsername);
      }}>
        <p>重置 {resetUser.username} 的密码</p><label htmlFor="team-new-password">新密码</label>
        <Input id="team-new-password" type="password" autoComplete="new-password" required minLength={12} value={newPassword} onChange={(e) => setNewPassword(e.target.value)} />
        <Button type="submit" disabled={busy || newPassword.length < 12}>保存新密码</Button>
        <Button type="button" variant="ghost" disabled={busy} onClick={() => { setResetUser(null); setNewPassword(""); }}>取消</Button>
      </form>}
    </DialogContent>
  </Dialog>;
}
