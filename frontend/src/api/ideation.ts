import { apiCall } from './client';

const root = (project: string) => `projects/${encodeURIComponent(project)}/creation`;

export interface IdeationCandidate {
  id: string;
  title: string;
  premise: string;
  protagonist_goal: string;
  conflict: string;
  hook: string;
  expansion: string;
}

export type IdeationCandidateBody = Omit<IdeationCandidate, 'id'>;

export interface IdeationObjectRef {
  object_id: string;
  revision: number;
}

export interface IdeationSource {
  output_id: string;
  operation_id: string;
  delegation_id: string;
  run_id: string | null;
  target_node_ids: string[];
  inputs: unknown[];
}

export interface IdeationSelection {
  object_id: string;
  revision: number;
  candidate_id: string;
  node_id: string;
  run_id: string | null;
  source: IdeationSource;
}

export interface IdeationObject {
  object_id: string;
  revision: number;
  origin: IdeationObjectRef;
  source: IdeationSource;
  candidates: IdeationCandidate[];
  selection: IdeationSelection | null;
}

export interface IdeationScope {
  canvas_id: string;
  conversation_id: string;
  node_id: string;
}

export interface IdeationPreviewRequest extends IdeationScope {
  action: 'edit' | 'adopt';
  expected_revision: number;
  candidate_id: string;
  candidate?: IdeationCandidateBody;
}

export interface IdeationPreview {
  proof: string;
  object_ref: IdeationObjectRef;
  candidate_id: string;
  action: 'edit' | 'adopt';
}

export interface IdeationWriteBase extends IdeationScope {
  request_id: string;
  expected_revision: number;
  candidate_id: string;
  proof: string;
}

export interface IdeationEditRequest extends IdeationWriteBase {
  candidate: IdeationCandidateBody;
}

export type IdeationAdoptRequest = IdeationWriteBase;

export interface IdeationWriteReceipt {
  request_id: string;
  status: 'applied' | 'unknown';
  operation_id?: string;
  object_ref?: IdeationObjectRef;
  candidate_id?: string;
  origin?: IdeationObjectRef;
  selection?: IdeationSelection | null;
}

export const getIdeation = (
  project: string,
  objectId: string,
  scope: IdeationScope & { revision?: number },
) => apiCall<IdeationObject>(`${root(project)}/ideation/${encodeURIComponent(objectId)}`, {
  searchParams: scope,
});

export const previewIdeation = (
  project: string,
  objectId: string,
  request: IdeationPreviewRequest,
) => apiCall<IdeationPreview>(`${root(project)}/ideation/${encodeURIComponent(objectId)}/preview`, {
  method: 'POST', json: request,
});

export const editIdeation = (
  project: string,
  objectId: string,
  request: IdeationEditRequest,
) => apiCall<IdeationWriteReceipt>(`${root(project)}/ideation/${encodeURIComponent(objectId)}/edit`, {
  method: 'POST', json: request,
});

export const adoptIdeation = (
  project: string,
  objectId: string,
  request: IdeationAdoptRequest,
) => apiCall<IdeationWriteReceipt>(`${root(project)}/ideation/${encodeURIComponent(objectId)}/adopt`, {
  method: 'POST', json: request,
});

export const getIdeationRequest = (
  project: string,
  requestId: string,
  scope: IdeationScope,
) => apiCall<IdeationWriteReceipt>(`${root(project)}/ideation-requests/${encodeURIComponent(requestId)}`, {
  searchParams: scope,
});
