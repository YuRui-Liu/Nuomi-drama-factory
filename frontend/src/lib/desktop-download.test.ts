// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  detectDesktopPlatform,
  FALLBACK_DOWNLOAD_URL,
  pickInstallerFromManifest,
  resolveDesktopDownloadUrl,
} from "./desktop-download";

it("falls back to the public Nuomi Drama Factory releases page", () => {
  expect(FALLBACK_DOWNLOAD_URL).toBe(
    "https://github.com/YuRui-Liu/Nuomi-drama-factory/releases/latest",
  );
});

// 与发布流水线产出的 electron-updater 清单同形状(url 字段带版本号文件名)。
const WINDOWS_MANIFEST = `version: 1.1.0
files:
  - url: DramaClaw-Setup-1.1.0.exe
    sha512: occFiM5M3gMp2RqWdM+5Fjw==
    size: 883116200
path: DramaClaw-Setup-1.1.0.exe
sha512: occFiM5M3gMp2RqWdM+5Fjw==
releaseDate: '2026-07-15T08:45:34.419Z'
`;

// macOS 清单同时列 zip(自动更新载体)与 dmg(首装载体)。
const MAC_MANIFEST = `version: 1.1.0
files:
  - url: DramaClaw-1.1.0-arm64.zip
    sha512: Bw4uOHg/lIXnqAlOKsuZMw==
    size: 1003619976
  - url: DramaClaw-1.1.0-arm64.dmg
    sha512: 5xw1uPGkX0Yl3m2n4o5p6q==
    size: 969342976
path: DramaClaw-1.1.0-arm64.zip
sha512: Bw4uOHg/lIXnqAlOKsuZMw==
releaseDate: '2026-07-15T08:45:34.419Z'
`;

describe("pickInstallerFromManifest", () => {
  it("picks the .exe from the Windows manifest", () => {
    expect(pickInstallerFromManifest(WINDOWS_MANIFEST, "windows")).toBe(
      "DramaClaw-Setup-1.1.0.exe",
    );
  });

  it("picks the .dmg (not the auto-update .zip) from the mac manifest", () => {
    expect(pickInstallerFromManifest(MAC_MANIFEST, "mac")).toBe(
      "DramaClaw-1.1.0-arm64.dmg",
    );
  });

  it("returns null when the wanted installer type is absent", () => {
    expect(pickInstallerFromManifest(WINDOWS_MANIFEST, "mac")).toBeNull();
    expect(pickInstallerFromManifest("", "windows")).toBeNull();
  });
});

describe("resolveDesktopDownloadUrl", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it.each([
    ["mac", "NuomiDrama-2.0.0-arm64.dmg"],
    ["windows", "Nuomi-Drama-Factory-Setup-2.0.0.exe"],
  ] as const)("selects a new-brand %s asset from the public GitHub release", async (platform, name) => {
    const browserDownloadUrl = `https://github.com/YuRui-Liu/Nuomi-drama-factory/releases/download/v2.0.0/${name}`;
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        assets: [{ name, browser_download_url: browserDownloadUrl }],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(resolveDesktopDownloadUrl(platform)).resolves.toBe(browserDownloadUrl);
    expect(browserDownloadUrl).not.toMatch(/DramaClaw/i);
    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.github.com/repos/YuRui-Liu/Nuomi-drama-factory/releases/latest",
      expect.objectContaining({ headers: { Accept: "application/vnd.github+json" } }),
    );
  });

  it("returns null for a non-ok GitHub response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 404 }));
    await expect(resolveDesktopDownloadUrl("mac")).resolves.toBeNull();
  });

  it("returns null when the GitHub request rejects", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    await expect(resolveDesktopDownloadUrl("windows")).resolves.toBeNull();
  });

  it("returns null when no new-brand installer matches the platform", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        assets: [
          {
            name: "DramaClaw-Setup-1.1.0.exe",
            browser_download_url: "https://github.com/YuRui-Liu/Nuomi-drama-factory/releases/download/v1.1.0/DramaClaw-Setup-1.1.0.exe",
          },
          {
            name: "NuomiDrama-2.0.0.zip",
            browser_download_url: "https://github.com/YuRui-Liu/Nuomi-drama-factory/releases/download/v2.0.0/NuomiDrama-2.0.0.zip",
          },
        ],
      }),
    }));

    await expect(resolveDesktopDownloadUrl("windows")).resolves.toBeNull();
  });

  it("rejects a matching asset whose download URL is outside the public repository", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        assets: [{
          name: "NuomiDrama-Setup-2.0.0.exe",
          browser_download_url: "https://evil.example/NuomiDrama-Setup-2.0.0.exe",
        }],
      }),
    }));

    await expect(resolveDesktopDownloadUrl("windows")).resolves.toBeNull();
  });

  it.each([
    "https://github.com:444/YuRui-Liu/Nuomi-drama-factory/releases/download/v2.0.0/NuomiDrama-Setup-2.0.0.exe",
    "https://github.com/YuRui-Liu/Nuomi-drama-factory/releases/download/v2.0.0/different-file.exe",
  ])("rejects a non-canonical or mismatched asset URL: %s", async (browserDownloadUrl) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        assets: [{
          name: "NuomiDrama-Setup-2.0.0.exe",
          browser_download_url: browserDownloadUrl,
        }],
      }),
    }));

    await expect(resolveDesktopDownloadUrl("windows")).resolves.toBeNull();
  });
});

describe("detectDesktopPlatform", () => {
  it("classifies Windows user agents", () => {
    expect(
      detectDesktopPlatform("Mozilla/5.0 (Windows NT 10.0; Win64; x64)"),
    ).toBe("windows");
  });

  it("falls back to mac for everything else", () => {
    expect(
      detectDesktopPlatform("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"),
    ).toBe("mac");
    expect(detectDesktopPlatform("")).toBe("mac");
  });
});
