// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * Desktop installer downloads offered on the login hero.
 *
 * Resolve current installers from the public Nuomi Drama Factory GitHub release.
 * Asset names must use a supported new-brand prefix and the download URL must
 * remain under this repository's GitHub release-download path.
 */
export type DesktopPlatform = "mac" | "windows";

const RELEASE_API_URL =
  "https://api.github.com/repos/YuRui-Liu/Nuomi-drama-factory/releases/latest";
const RELEASE_DOWNLOAD_PATH =
  "/YuRui-Liu/Nuomi-drama-factory/releases/download/";

// latest-mac.yml 同时列出 zip(自动更新的载体)与 dmg(首次安装的载体),
// 官网必须发 dmg;Windows 清单里只有 exe。
const INSTALLER_EXT: Record<DesktopPlatform, string> = {
  mac: ".dmg",
  windows: ".exe",
};

/**
 * 指针解析失败(断网、CDN 故障、清单格式漂移)时的兜底:GitHub Releases
 * 页含全部平台资产,慢但可达,按钮永远不会点了没反应。
 */
export const FALLBACK_DOWNLOAD_URL =
  "https://github.com/YuRui-Liu/Nuomi-drama-factory/releases/latest";

/** Legacy manifest compatibility parser; no longer used by the public download path. */
export function pickInstallerFromManifest(
  manifest: string,
  platform: DesktopPlatform,
): string | null {
  const ext = INSTALLER_EXT[platform];
  for (const match of manifest.matchAll(/url:\s*(\S+)/g)) {
    if (match[1].endsWith(ext)) return match[1];
  }
  return null;
}

type ReleaseAsset = {
  name?: unknown;
  browser_download_url?: unknown;
};

function isPublicReleaseDownload(url: string, assetName: string): boolean {
  try {
    const parsed = new URL(url);
    const pathSegments = parsed.pathname.split("/");
    const encodedName = pathSegments[pathSegments.length - 1];
    return parsed.origin === "https://github.com"
      && parsed.pathname.startsWith(RELEASE_DOWNLOAD_PATH)
      && typeof encodedName === "string"
      && decodeURIComponent(encodedName) === assetName;
  } catch {
    return false;
  }
}

/** Resolve a current new-brand installer; callers use the Releases fallback on failure. */
export async function resolveDesktopDownloadUrl(
  platform: DesktopPlatform,
): Promise<string | null> {
  try {
    const res = await fetch(RELEASE_API_URL, {
      headers: { Accept: "application/vnd.github+json" },
    });
    if (!res.ok) return null;
    const data = await res.json() as { assets?: unknown };
    if (!Array.isArray(data.assets)) return null;
    const ext = INSTALLER_EXT[platform];
    const asset = (data.assets as ReleaseAsset[]).find((candidate) => {
      if (typeof candidate.name !== "string") return false;
      if (typeof candidate.browser_download_url !== "string") return false;
      return /^(?:NuomiDrama|Nuomi-Drama-Factory)(?:[-_.]|$)/i.test(candidate.name)
        && candidate.name.toLowerCase().endsWith(ext)
        && isPublicReleaseDownload(candidate.browser_download_url, candidate.name);
    });
    return typeof asset?.browser_download_url === "string"
      ? asset.browser_download_url
      : null;
  } catch {
    return null;
  }
}

/**
 * Which installer to feature as the filled primary button. Falls back to macOS
 * on anything we can't identify (Linux, phones, bots) so the row never renders
 * empty — the other platform stays one click away as the adjacent text link.
 */
export function detectDesktopPlatform(
  userAgent: string = typeof navigator === "undefined" ? "" : navigator.userAgent,
): DesktopPlatform {
  return /windows|win32|win64/i.test(userAgent) ? "windows" : "mac";
}
