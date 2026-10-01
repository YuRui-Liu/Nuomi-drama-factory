export interface MusicClip {
    id: string;
    assetVersionId: string;
    startMs: number;
    sourceInMs: number;
    lengthMs: number;
    gainDb: number;
    fadeInMs: number;
    fadeOutMs: number;
    loop: {
        startMs: number;
        endMs: number;
    } | null;
}
export interface MusicTrack {
    id: string;
    name: string;
    gainDb: number;
    muted: boolean;
    solo: boolean;
    clips: MusicClip[];
}
export interface MusicSource {
    assetVersionId: string;
    sha256: string;
    durationMs: number;
}
export interface MusicPlan {
    schemaVersion: 1;
    revision: number;
    source: MusicSource;
    original: {
        muted: boolean;
        gainDb: number;
    };
    ducking: {
        enabled: boolean;
        gainDb: number;
        attackMs: number;
        releaseMs: number;
        intervals: {
            startMs: number;
            endMs: number;
        }[];
    };
    tracks: MusicTrack[];
}
export interface MusicAsset {
    id: string;
    versionId: string;
    name: string;
    durationMs: number;
    url: string;
    sha256?: string;
    revision: number;
    tags: string[];
    vocals: string;
    description?: string;
    matchReasons?: string[];
    shortcomings?: string[];
}
export interface MusicJob {
    id: string;
    kind: string;
    status: string;
    error?: string;
    audioUrl?: string;
    videoUrl?: string;
    candidate?: MusicAsset;
    remoteTaskId?: string;
}
