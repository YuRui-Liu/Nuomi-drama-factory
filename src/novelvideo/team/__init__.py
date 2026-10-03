"""Built-in single-team edition."""


def register_team_ports():
    from novelvideo.ports.local import register_local_ports
    from novelvideo.ports.registry import register_port
    from novelvideo.team.store import TeamStore
    from novelvideo.team.adapters import TeamAuth, TeamAgentSessions, TeamProjectRegistry, TeamProjectAccess, TeamLifecycle

    store = TeamStore()
    register_local_ports()
    register_port("team_store", store)
    register_port("auth", TeamAuth(store))
    register_port("auth_session", TeamAgentSessions(store))
    register_port("project_registry", TeamProjectRegistry(store))
    register_port("project_access", TeamProjectAccess(store))
    register_port("lifecycle", TeamLifecycle(store))
