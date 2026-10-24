"""ROG Ally family detection from the BIOS product name."""
import sys
from typing import Callable, Optional

BIOS_KEY = r"HARDWARE\DESCRIPTION\System\BIOS"
BIOS_VALUE = "SystemProductName"
ALLY_MARKER = "RC7"


def _read_registry_product() -> str:
    import winreg  # type: ignore  # Windows only

    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, BIOS_KEY) as key:
        value, _ = winreg.QueryValueEx(key, BIOS_VALUE)
    return str(value)


def product_name(read_product: Optional[Callable[[], str]] = None) -> str:
    """BIOS product name, or "" when it cannot be read."""
    reader = read_product
    if reader is None:
        if sys.platform != "win32":
            return ""
        reader = _read_registry_product
    try:
        return str(reader() or "").strip()
    except Exception:
        return ""


def is_ally(read_product: Optional[Callable[[], str]] = None) -> bool:
    return ALLY_MARKER in product_name(read_product)
