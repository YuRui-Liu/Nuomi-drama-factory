import { describe, expect, it } from 'vitest';
import { agentReadiness, runtimeReadiness } from '@/features/agent-runtime/agentReadiness';

describe('agent readiness priority', () => {
  const ready = { unknown: false, running: false, hasAgent: true, modelAvailable: true, hasScope: true };
  it('requires agent, model and explicit scope in that order', () => {
    expect(agentReadiness(ready)).toBe('ready');
    expect(agentReadiness({ ...ready, hasScope: false })).toBe('choose_scope');
    expect(agentReadiness({ ...ready, hasScope: false, modelAvailable: false })).toBe('configure_model');
    expect(agentReadiness({ ...ready, hasAgent: false, modelAvailable: false })).toBe('choose_agent');
  });
  it('prioritizes uncertain submission over running and configuration', () => {
    expect(agentReadiness({ ...ready, unknown: true, running: true, hasAgent: false })).toBe('unknown');
    expect(agentReadiness({ ...ready, running: true, hasAgent: false })).toBe('running');
  });
  it('does not advertise readiness before a successful status read or without captured scope', () => {
    const runtime = {
      statusKnown: false, intent: null, busy: false, main: { current_agent: {} },
      runtime: { available: true }, nodes: [],
    } as unknown as Parameters<typeof runtimeReadiness>[0];
    expect(runtimeReadiness(runtime)).toBe('unknown');
    expect(runtimeReadiness({ ...runtime, statusKnown: true })).toBe('choose_scope');
    expect(runtimeReadiness({ ...runtime, statusKnown: true, nodes: [{ id: 'n', title: '引用节点' }] })).toBe('ready');
  });
  it('keeps a configured but unverified model out of the ready state', () => {
    const runtime = {
      statusKnown: true, intent: null, busy: false, main: { current_agent: {} },
      runtime: { available: true, code: 'configured_not_verified' },
      nodes: [{ id: 'n', title: '引用节点' }],
    } as unknown as Parameters<typeof runtimeReadiness>[0];
    expect(runtimeReadiness(runtime)).toBe('model_unverified');
  });
});
