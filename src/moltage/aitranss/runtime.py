"""Server-generic AITRANSS identity and Step-4 resource defaults."""

import re


AITRANSS_EXECUTABLE_COMMAND = "aitranss.x"
AITRANSS_DEFAULT_CPU_THREADS = 1
AITRANSS_DEFAULT_RUNTIME_MINUTES = 10 * 60
AITRANSS_DEFAULT_MEMORY_GB = 100

_AITRANSS_EXECUTABLE_NAME = re.compile(
    r"aitranss(?:[._-][A-Za-z0-9]+)*\.x|aitranss",
    re.IGNORECASE,
)


def is_aitranss_executable_name(name: str) -> bool:
    """Return whether a basename belongs to the supported AITRANSS family."""

    return isinstance(name, str) and _AITRANSS_EXECUTABLE_NAME.fullmatch(name) is not None
