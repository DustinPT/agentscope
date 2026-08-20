# -*- coding: utf-8 -*-
"""The workspace module in agentscope."""


from ._agent_workspace import AgentWorkspaceView
from ._base import WorkspaceBase
from ._local_workspace import LocalWorkspace
from ._offload_protocol import Offloader
from ._docker import DockerBackend, DockerWorkspace
from ._e2b import E2BBackend, E2BWorkspace
from ._srt import SRTBackend, SRTWorkspace


__all__ = [
    "WorkspaceBase",
    "AgentWorkspaceView",
    "LocalWorkspace",
    "DockerBackend",
    "DockerWorkspace",
    "E2BBackend",
    "E2BWorkspace",
    "SRTBackend",
    "SRTWorkspace",
    "Offloader",
]
