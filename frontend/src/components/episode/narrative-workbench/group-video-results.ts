// SPDX-License-Identifier: Elastic-2.0

type VideoSubmissionResponse = { ok: boolean; error?: string };

export interface GroupVideoFailure {
  beatNum: number;
  error: string;
}

export function groupVideoFailures(
  beatNumbers: readonly number[],
  results: readonly PromiseSettledResult<VideoSubmissionResponse>[],
): GroupVideoFailure[] {
  return results.flatMap((result, index) => {
    const beatNum = beatNumbers[index];
    if (beatNum === undefined) return [];
    if (result.status === "rejected") {
      return [{ beatNum, error: result.reason instanceof Error ? result.reason.message : String(result.reason || "任务提交失败") }];
    }
    if (!result.value.ok) {
      return [{ beatNum, error: result.value.error || "任务提交失败" }];
    }
    return [];
  });
}
