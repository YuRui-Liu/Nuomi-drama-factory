// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { NodeTypes } from '@xyflow/react';

import { AudioNode } from './AudioNode';
import { AgentNode } from './AgentNode';
import { IdeationNode } from './IdeationNode';
import { ScreenplayNode } from './ScreenplayNode';
import { DirectorWorkspaceNode } from './DirectorWorkspaceNode';
import { NarrativeGroupStoryboardNode } from './NarrativeGroupStoryboardNode';
import { NarrativeGroupVideoNode } from './NarrativeGroupVideoNode';
import { EpisodeComposeNode } from './EpisodeComposeNode';
import { CharacterVoiceNode } from './CharacterVoiceNode';
import { MusicDeskNode } from './MusicDeskNode';
import { BeatContextNode } from './BeatContextNode';
import { GroupNode } from './GroupNode';
import { ImageEditNode } from './ImageEditNode';
import { ImageGenNode } from './ImageGenNode';
import { ImageNode } from './ImageNode';
import { Pano360ViewerNode } from './Pano360ViewerNode';
import { ScriptNode } from './ScriptNode';
import { SkillNode } from './SkillNode';
import { StoryboardGenNode } from './StoryboardGenNode';
import { StoryboardNode } from './StoryboardNode';
import { TextAnnotationNode } from './TextAnnotationNode';
import { ThreeDWorldNode } from './ThreeDWorldNode';
import { UploadNode } from './UploadNode';
import { VideoComposeNode } from './VideoComposeNode';
import { VideoDirectorNode } from './VideoDirectorNode';
import { VideoNode } from './VideoNode';
import { VideoStoryNode } from './VideoStoryNode';

export const nodeTypes: NodeTypes = {
  agentNode: AgentNode,
  ideationNode: IdeationNode,
  screenplayNode: ScreenplayNode,
  directorWorkspaceNode: DirectorWorkspaceNode,
  narrativeGroupStoryboardNode: NarrativeGroupStoryboardNode,
  narrativeGroupVideoNode: NarrativeGroupVideoNode,
  episodeComposeNode: EpisodeComposeNode,
  characterVoiceNode: CharacterVoiceNode,
  musicDeskNode: MusicDeskNode,
  audioNode: AudioNode,
  beatContextNode: BeatContextNode,
  exportImageNode: ImageNode,
  groupNode: GroupNode,
  imageGenNode: ImageGenNode,
  imageNode: ImageEditNode,
  pano360ViewerNode: Pano360ViewerNode,
  scriptNode: ScriptNode,
  skillNode: SkillNode,
  storyboardGenNode: StoryboardGenNode,
  storyboardNode: StoryboardNode,
  textAnnotationNode: TextAnnotationNode,
  threeDWorldNode: ThreeDWorldNode,
  uploadNode: UploadNode,
  videoComposeNode: VideoComposeNode,
  videoDirectorNode: VideoDirectorNode,
  videoNode: VideoNode,
  videoStoryNode: VideoStoryNode,
};

export {
  AgentNode,
  AudioNode,
  BeatContextNode,
  CharacterVoiceNode,
  DirectorWorkspaceNode,
  GroupNode,
  IdeationNode,
  ImageEditNode,
  ImageGenNode,
  ImageNode,
  NarrativeGroupStoryboardNode,
  NarrativeGroupVideoNode,
  EpisodeComposeNode,
  Pano360ViewerNode,
  ScreenplayNode,
  ScriptNode,
  SkillNode,
  StoryboardGenNode,
  StoryboardNode,
  TextAnnotationNode,
  ThreeDWorldNode,
  UploadNode,
  VideoComposeNode,
  VideoDirectorNode,
  VideoNode,
  VideoStoryNode,
};
