"""Which application system a posting uses, and that system's fetch / fill / submit."""

from dataclasses import dataclass
from types import ModuleType
from typing import Callable

from . import ashby, greenhouse_api, greenhouse_form


@dataclass
class System:
    name: str
    matches: Callable[[str], bool]
    fetch_job: Callable
    fill: Callable
    submit: Callable


def _module_system(name: str, m: ModuleType) -> System:
    return System(name, m.matches, m.fetch_job, m.fill, m.submit)


SYSTEMS = [
    _module_system("Ashby", ashby),
    System("Greenhouse", lambda url: greenhouse_api.parse_url(url) is not None,
           greenhouse_api.fetch_job, greenhouse_form.fill, greenhouse_form.submit),
]


def system_for(url: str) -> System | None:
    return next((s for s in SYSTEMS if s.matches(url)), None)
