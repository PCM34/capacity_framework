"""Plugin-style registry of tools shown in the hub's sidebar.

A "page" module registers itself by decorating its render function with
@register(...). The hub discovers tools purely by which page modules have
been imported - see hub/main.py for the import list that activates them.
"""
from dataclasses import dataclass
from typing import Callable

RenderFn = Callable[[], None]


@dataclass(frozen=True)
class Tool:
    key: str
    title: str
    description: str
    icon: str
    render: RenderFn


_tools: dict[str, Tool] = {}


def register(key: str, title: str, description: str = "", icon: str = "widgets"):
    """Decorator that registers a NiceGUI render function as a hub tool.

    Usage:
        @register(key="capacity_model", title="Capacity Model", icon="calculate")
        def render():
            ui.label("...")
    """
    def decorator(render_fn: RenderFn) -> RenderFn:
        if key in _tools:
            raise ValueError(f"A tool with key '{key}' is already registered")
        _tools[key] = Tool(key=key, title=title, description=description, icon=icon, render=render_fn)
        return render_fn
    return decorator


def all_tools() -> list[Tool]:
    return sorted(_tools.values(), key=lambda t: t.title)


def get(key: str) -> Tool | None:
    return _tools.get(key)
